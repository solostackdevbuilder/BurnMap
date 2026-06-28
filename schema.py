import sqlite3


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id      TEXT PRIMARY KEY,
            provider        TEXT DEFAULT 'claude',
            backend_provider TEXT,
            client          TEXT DEFAULT 'cli',
            project_name    TEXT,
            first_timestamp TEXT,
            last_timestamp  TEXT,
            git_branch      TEXT,
            total_input_tokens      INTEGER DEFAULT 0,
            total_output_tokens     INTEGER DEFAULT 0,
            total_cache_read        INTEGER DEFAULT 0,
            total_cache_creation    INTEGER DEFAULT 0,
            total_native_cost       REAL DEFAULT 0,
            tree_nodes              INTEGER DEFAULT 0,
            tree_edges              INTEGER DEFAULT 0,
            tree_max_depth          INTEGER DEFAULT 0,
            tree_branch_points      INTEGER DEFAULT 0,
            tree_leaf_count         INTEGER DEFAULT 0,
            tree_root_count         INTEGER DEFAULT 0,
            model           TEXT,
            turn_count      INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS turns (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id              TEXT,
            provider                TEXT DEFAULT 'claude',
            backend_provider        TEXT,
            client                  TEXT DEFAULT 'cli',
            timestamp               TEXT,
            model                   TEXT,
            input_tokens            INTEGER DEFAULT 0,
            output_tokens           INTEGER DEFAULT 0,
            cache_read_tokens       INTEGER DEFAULT 0,
            cache_creation_tokens   INTEGER DEFAULT 0,
            native_cost             REAL DEFAULT 0,
            tool_call_count         INTEGER DEFAULT 0,
            tool_name               TEXT,
            tool_names              TEXT,
            cwd                     TEXT,
            message_id              TEXT,
            parent_message_id       TEXT
        );

        CREATE TABLE IF NOT EXISTS pi_messages (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id          TEXT,
            message_id          TEXT,
            parent_message_id   TEXT,
            role                TEXT,
            timestamp           TEXT,
            provider            TEXT,
            model               TEXT,
            tool_names          TEXT,
            text_preview        TEXT,
            depth               INTEGER DEFAULT 0,
            child_count         INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS processed_files (
            path    TEXT PRIMARY KEY,
            mtime   REAL,
            lines   INTEGER
        );
    """)

    for table, column, definition in [
        ("sessions", "provider", "TEXT DEFAULT 'claude'"),
        ("sessions", "client", "TEXT DEFAULT 'cli'"),
        ("sessions", "backend_provider", "TEXT"),
        ("turns", "provider", "TEXT DEFAULT 'claude'"),
        ("turns", "backend_provider", "TEXT"),
        ("turns", "client", "TEXT DEFAULT 'cli'"),
        ("turns", "message_id", "TEXT"),
        ("turns", "parent_message_id", "TEXT"),
        ("sessions", "total_native_cost", "REAL DEFAULT 0"),
        ("sessions", "tree_nodes", "INTEGER DEFAULT 0"),
        ("sessions", "tree_edges", "INTEGER DEFAULT 0"),
        ("sessions", "tree_max_depth", "INTEGER DEFAULT 0"),
        ("sessions", "tree_branch_points", "INTEGER DEFAULT 0"),
        ("sessions", "tree_leaf_count", "INTEGER DEFAULT 0"),
        ("sessions", "tree_root_count", "INTEGER DEFAULT 0"),
        ("turns", "native_cost", "REAL DEFAULT 0"),
        ("turns", "tool_call_count", "INTEGER DEFAULT 0"),
        ("turns", "tool_names", "TEXT"),
    ]:
        try:
            conn.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_timestamp ON turns(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_turns_provider ON turns(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_first ON sessions(first_timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_provider ON sessions(provider)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_messages_session ON pi_messages(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_messages_parent ON pi_messages(parent_message_id)")
    # Pi session ids are not globally unique (same short id can repeat across sessions),
    # so this must be scoped to (session_id, message_id) or INSERT OR IGNORE will silently
    # drop turns from different sessions that happen to share a message id.
    conn.execute("DROP INDEX IF EXISTS idx_turns_message_id")
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_turns_message_id
        ON turns(session_id, message_id) WHERE message_id IS NOT NULL AND message_id != ''
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pi_messages_unique
        ON pi_messages(session_id, message_id) WHERE message_id IS NOT NULL AND message_id != ''
    """)
    conn.commit()
