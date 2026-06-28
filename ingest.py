import os
import sys
import time
from pathlib import Path

from paths import resolve_db_path
from schema import init_db
from sources import get_sources
from sources.base import ParseResult
from store import (
    aggregate_sessions,
    connect_db,
    get_processed_file,
    recompute_session_totals,
    save_pi_messages,
    save_processed_file,
    save_sessions,
    save_turns,
)


def _atomic_replace_with_retry(src: Path, dst: Path, attempts: int = 5):
    """Atomically replace `dst` with `src`, retrying on Windows sharing violations."""
    is_windows = sys.platform.startswith("win")
    delay = 0.1
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not is_windows or attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def scan_all_sources(projects_dir=None, projects_dirs=None, db_path=None, verbose=True, sources=None):
    conn = connect_db(db_path)
    init_db(conn)

    # `sources is None` means "use defaults". An explicit empty list means
    # "scan no sources" (useful for tests and for an agent-less install).
    sources = get_sources() if sources is None else sources
    new_files = 0
    updated_files = 0
    skipped_files = 0
    total_turns = 0
    total_sessions = set()

    for source in sources:
        discovered = source.discover_files(projects_dir=projects_dir, projects_dirs=projects_dirs)
        if verbose and discovered:
            print(f"Scanning {source.display_name} ({source.provider}/{source.client}) ...")

        for filepath in discovered:
            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue

            source_key = source.source_key(filepath)
            row = get_processed_file(conn, source_key)
            if row and abs(row["mtime"] - mtime) < 0.01:
                skipped_files += 1
                continue

            is_new = row is None
            if verbose:
                print(f"  [{'NEW' if is_new else 'UPD'}] {filepath}")

            if is_new:
                session_records, turns, message_nodes, line_count = source.parse_full_file(filepath)
                if session_records or turns or message_nodes:
                    sessions = aggregate_sessions(session_records, turns)
                    save_sessions(conn, sessions)
                    save_turns(conn, turns)
                    if message_nodes:
                        for session_record in session_records:
                            save_pi_messages(conn, session_record["session_id"], [n for n in message_nodes if n["session_id"] == session_record["session_id"]])
                    for session in sessions:
                        total_sessions.add(session["session_id"])
                    total_turns += len(turns)
                    new_files += 1
            else:
                old_lines = row["lines"] if row else 0
                session_records, turns, message_nodes, line_count = source.parse_incremental_file(filepath, old_lines)
                if line_count <= old_lines:
                    save_processed_file(conn, source_key, mtime, line_count)
                    conn.commit()
                    skipped_files += 1
                    continue
                if session_records or turns or message_nodes:
                    sessions = aggregate_sessions(session_records, turns)
                    save_sessions(conn, sessions)
                    save_turns(conn, turns)
                    if message_nodes:
                        for session_record in session_records:
                            save_pi_messages(conn, session_record["session_id"], [n for n in message_nodes if n["session_id"] == session_record["session_id"]])
                    for session in sessions:
                        total_sessions.add(session["session_id"])
                    total_turns += len(turns)
                updated_files += 1

            save_processed_file(conn, source_key, mtime, line_count)
            conn.commit()

    if new_files or updated_files:
        # Scope the recompute to sessions that actually saw new turns. On a
        # 5K-session DB this turns an O(all) sweep into O(touched).
        recompute_session_totals(conn, session_ids=total_sessions)

    if verbose:
        print("\nScan complete:")
        print(f"  New files:     {new_files}")
        print(f"  Updated files: {updated_files}")
        print(f"  Skipped files: {skipped_files}")
        print(f"  Turns added:   {total_turns}")
        print(f"  Sessions seen: {len(total_sessions)}")

    conn.close()
    return {"new": new_files, "updated": updated_files, "skipped": skipped_files, "turns": total_turns, "sessions": len(total_sessions)}


def rebuild_database(projects_dir=None, projects_dirs=None, db_path=None, verbose=True, sources=None):
    """Rebuild the DB from scratch into a temp file, then atomically swap it in."""
    destination = Path(db_path) if db_path else resolve_db_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".rescan.tmp")
    try:
        if temp_path.exists():
            temp_path.unlink()
        result = scan_all_sources(
            projects_dir=projects_dir,
            projects_dirs=projects_dirs,
            db_path=temp_path,
            verbose=verbose,
            sources=sources,
        )
        _atomic_replace_with_retry(temp_path, destination)
        return result
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    import sys

    projects_dir = None
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--projects-dir" and i + 1 < len(sys.argv[1:]):
            projects_dir = Path(sys.argv[i + 2])
            break
    scan_all_sources(projects_dir=projects_dir)
