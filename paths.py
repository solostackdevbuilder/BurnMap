import shutil
import sqlite3
from pathlib import Path

APP_HOME = Path.home() / ".coding-agents"
DB_PATH = APP_HOME / "usage.db"
LEGACY_DB_PATH = Path.home() / ".claude" / "usage.db"
STATIC_DIR = Path(__file__).parent / "static"

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_XCODE_PROJECTS_DIR = Path.home() / "Library" / "Developer" / "Xcode" / "CodingAssistant" / "ClaudeAgentConfig" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
PI_SESSIONS_DIR = Path.home() / ".pi" / "agent" / "sessions"
OPENCODE_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def ensure_app_home():
    APP_HOME.mkdir(parents=True, exist_ok=True)
    return APP_HOME


def migrate_legacy_db():
    ensure_app_home()
    if DB_PATH.exists() or not LEGACY_DB_PATH.exists():
        return DB_PATH
    shutil.copy2(LEGACY_DB_PATH, DB_PATH)
    # Upgrade the copied DB to the current schema (adds new columns/indices
    # and repairs session/processed-file keys the legacy schema didn't use).
    from schema import init_db
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        # Legacy sessions.session_id had raw IDs; new code normalizes to "provider:id".
        conn.execute("UPDATE sessions SET session_id = 'claude:' || session_id WHERE session_id NOT LIKE '%:%'")
        conn.execute("UPDATE turns SET session_id = 'claude:' || session_id WHERE session_id NOT LIKE '%:%'")
        conn.execute("UPDATE pi_messages SET session_id = 'claude:' || session_id WHERE session_id NOT LIKE '%:%'")
        conn.execute("UPDATE processed_files SET path = 'claude:' || path WHERE path NOT LIKE '%:%'")
        conn.commit()
    finally:
        conn.close()
    return DB_PATH


def resolve_db_path():
    ensure_app_home()
    if DB_PATH.exists():
        return DB_PATH
    if LEGACY_DB_PATH.exists() and not DB_PATH.exists():
        return migrate_legacy_db()
    return DB_PATH
