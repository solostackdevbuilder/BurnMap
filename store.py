import sqlite3
from collections import defaultdict

from paths import resolve_db_path


def connect_db(db_path=None):
    path = db_path or resolve_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def aggregate_sessions(session_records, turns):
    session_stats = defaultdict(lambda: {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_read": 0,
        "total_cache_creation": 0,
        "total_native_cost": 0.0,
        "turn_count": 0,
        "model": None,
        "backend_provider": None,
    })

    for turn in turns:
        stats = session_stats[turn["session_id"]]
        stats["total_input_tokens"] += turn["input_tokens"]
        stats["total_output_tokens"] += turn["output_tokens"]
        stats["total_cache_read"] += turn["cache_read_tokens"]
        stats["total_cache_creation"] += turn["cache_creation_tokens"]
        stats["total_native_cost"] += turn.get("native_cost", 0.0) or 0.0
        stats["turn_count"] += 1
        if turn["model"]:
            stats["model"] = turn["model"]
        if turn.get("backend_provider"):
            stats["backend_provider"] = turn["backend_provider"]

    return [{**record, **session_stats[record["session_id"]]} for record in session_records]


def save_sessions(conn, sessions):
    for session in sessions:
        existing = conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            (session["session_id"],),
        ).fetchone()

        if existing is None:
            conn.execute(
                """
                INSERT INTO sessions
                    (session_id, provider, backend_provider, client, project_name, first_timestamp, last_timestamp,
                     git_branch, total_input_tokens, total_output_tokens,
                     total_cache_read, total_cache_creation, total_native_cost,
                     tree_nodes, tree_edges, tree_max_depth, tree_branch_points, tree_leaf_count, tree_root_count,
                     model, turn_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["session_id"], session.get("provider", "claude"), session.get("backend_provider", ""), session.get("client", "cli"), session["project_name"],
                    session["first_timestamp"], session["last_timestamp"], session["git_branch"],
                    session["total_input_tokens"], session["total_output_tokens"], session["total_cache_read"],
                    session["total_cache_creation"], session.get("total_native_cost", 0.0),
                    session.get("tree_nodes", 0), session.get("tree_edges", 0), session.get("tree_max_depth", 0),
                    session.get("tree_branch_points", 0), session.get("tree_leaf_count", 0), session.get("tree_root_count", 0),
                    session["model"], session["turn_count"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE sessions SET
                    provider = COALESCE(provider, ?),
                    backend_provider = COALESCE(backend_provider, ?),
                    client = COALESCE(client, ?),
                    project_name = COALESCE(project_name, ?),
                    first_timestamp = COALESCE(first_timestamp, ?),
                    last_timestamp = MAX(last_timestamp, ?),
                    git_branch = COALESCE(git_branch, ?),
                    total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    total_cache_read = total_cache_read + ?,
                    total_cache_creation = total_cache_creation + ?,
                    total_native_cost = total_native_cost + ?,
                    turn_count = turn_count + ?,
                    tree_nodes = COALESCE(?, tree_nodes),
                    tree_edges = COALESCE(?, tree_edges),
                    tree_max_depth = COALESCE(?, tree_max_depth),
                    tree_branch_points = COALESCE(?, tree_branch_points),
                    tree_leaf_count = COALESCE(?, tree_leaf_count),
                    tree_root_count = COALESCE(?, tree_root_count),
                    model = COALESCE(?, model)
                WHERE session_id = ?
                """,
                (
                    session.get("provider", "claude"), session.get("backend_provider", ""), session.get("client", "cli"), session["project_name"],
                    session["first_timestamp"], session["last_timestamp"], session["git_branch"],
                    session["total_input_tokens"], session["total_output_tokens"], session["total_cache_read"],
                    session["total_cache_creation"], session.get("total_native_cost", 0.0), session["turn_count"],
                    session.get("tree_nodes"), session.get("tree_edges"), session.get("tree_max_depth"),
                    session.get("tree_branch_points"), session.get("tree_leaf_count"), session.get("tree_root_count"),
                    session["model"], session["session_id"],
                ),
            )


def save_turns(conn, turns):
    conn.executemany(
        """
        INSERT OR IGNORE INTO turns
            (session_id, provider, backend_provider, client, timestamp, model, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, native_cost, tool_call_count, tool_name, tool_names, cwd, message_id, parent_message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                turn["session_id"], turn.get("provider", "claude"), turn.get("backend_provider", ""), turn.get("client", "cli"), turn["timestamp"],
                turn["model"], turn["input_tokens"], turn["output_tokens"], turn["cache_read_tokens"],
                turn["cache_creation_tokens"], turn.get("native_cost", 0.0), turn.get("tool_call_count", 0),
                turn["tool_name"], turn.get("tool_names", ""), turn["cwd"], turn.get("message_id", ""), turn.get("parent_message_id", ""),
            )
            for turn in turns
        ],
    )


def get_known_turn_message_ids(conn, session_id):
    """Return the set of non-empty message_ids already persisted for a session.

    Used by Codex incremental scans so we skip emitting turns the DB already
    knows about (the unique index would reject them anyway via INSERT OR
    IGNORE, but the pre-filter avoids N B-tree lookups per scan on
    already-seen turns).
    """
    rows = conn.execute(
        "SELECT message_id FROM turns WHERE session_id = ? AND message_id IS NOT NULL AND message_id != ''",
        (session_id,),
    ).fetchall()
    return {row["message_id"] for row in rows}


def save_pi_messages(conn, session_id, messages):
    conn.execute("DELETE FROM pi_messages WHERE session_id = ?", (session_id,))
    if not messages:
        return
    conn.executemany(
        """
        INSERT OR REPLACE INTO pi_messages
            (session_id, message_id, parent_message_id, role, timestamp, provider, model, tool_names, text_preview, depth, child_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                message["session_id"], message.get("message_id", ""), message.get("parent_message_id", ""), message.get("role", ""),
                message.get("timestamp", ""), message.get("provider", ""), message.get("model", ""), message.get("tool_names", ""),
                message.get("text_preview", ""), message.get("depth", 0), message.get("child_count", 0),
            )
            for message in messages
        ],
    )


def get_processed_file(conn, source_key):
    return conn.execute("SELECT mtime, lines FROM processed_files WHERE path = ?", (source_key,)).fetchone()


def save_processed_file(conn, source_key, mtime, line_count):
    conn.execute(
        "INSERT OR REPLACE INTO processed_files (path, mtime, lines) VALUES (?, ?, ?)",
        (source_key, mtime, line_count),
    )


def recompute_session_totals(conn, session_ids=None):
    """Refresh sessions.total_* from the turns table.

    Pass `session_ids` (iterable of session ids) to limit the update to
    sessions that actually changed in this scan. None means "recompute all"
    and is used for full-rebuild flows like /api/rescan into a fresh temp DB.

    The correlated subqueries still run per targeted session, so scoping is
    O(touched) not O(all_sessions) — on a 5K-session DB with 10 touched,
    this is ~500x faster than the unscoped version.
    """
    if session_ids is None:
        conn.execute("""
            UPDATE sessions SET
                total_input_tokens = COALESCE((SELECT SUM(input_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_output_tokens = COALESCE((SELECT SUM(output_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_cache_read = COALESCE((SELECT SUM(cache_read_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_cache_creation = COALESCE((SELECT SUM(cache_creation_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_native_cost = COALESCE((SELECT SUM(native_cost) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                turn_count = COALESCE((SELECT COUNT(*) FROM turns WHERE turns.session_id = sessions.session_id), 0)
        """)
    else:
        ids = tuple(session_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"""
            UPDATE sessions SET
                total_input_tokens = COALESCE((SELECT SUM(input_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_output_tokens = COALESCE((SELECT SUM(output_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_cache_read = COALESCE((SELECT SUM(cache_read_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_cache_creation = COALESCE((SELECT SUM(cache_creation_tokens) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                total_native_cost = COALESCE((SELECT SUM(native_cost) FROM turns WHERE turns.session_id = sessions.session_id), 0),
                turn_count = COALESCE((SELECT COUNT(*) FROM turns WHERE turns.session_id = sessions.session_id), 0)
            WHERE session_id IN ({placeholders})
            """,
            ids,
        )
    conn.commit()
