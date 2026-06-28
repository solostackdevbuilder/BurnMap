from typing import NamedTuple


class ParseResult(NamedTuple):
    """What every source parser returns.

    session_records: list of session-dict records (see sources.claude for shape)
    turns:           list of turn-dict records (see store.save_turns for columns)
    message_nodes:   list of Pi-only conversation-tree message nodes, or []
    line_count:      total lines read from the file (used for incremental skip)
    """
    session_records: list
    turns: list
    message_nodes: list
    line_count: int


class UsageSource:
    provider = "unknown"
    client = "unknown"
    display_name = "Unknown"

    def discover_files(self, projects_dir=None, projects_dirs=None):
        return []

    def parse_full_file(self, filepath) -> ParseResult:
        raise NotImplementedError

    def parse_incremental_file(self, filepath, old_lines, skip_turn_ids=None) -> ParseResult:
        # `skip_turn_ids` is an optional set of message ids already persisted
        # for any session this source owns; sources that track turn state
        # across lines (e.g., Codex) use it to avoid re-emitting known turns.
        # Sources that do line-offset incremental (Claude, Pi) can ignore it.
        raise NotImplementedError

    def source_key(self, filepath):
        return f"{self.provider}:{filepath}"


def project_name_from_cwd(cwd):
    if not cwd:
        return "unknown"
    parts = cwd.replace("\\", "/").rstrip("/").split("/")
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else "unknown"


def normalize_session_id(provider, raw_session_id):
    return f"{provider}:{raw_session_id}"


def update_session_record(session_record, record):
    timestamp = record.get("timestamp", "")
    git_branch = record.get("gitBranch", "")
    if timestamp and (not session_record["first_timestamp"] or timestamp < session_record["first_timestamp"]):
        session_record["first_timestamp"] = timestamp
    if timestamp and (not session_record["last_timestamp"] or timestamp > session_record["last_timestamp"]):
        session_record["last_timestamp"] = timestamp
    if git_branch and not session_record["git_branch"]:
        session_record["git_branch"] = git_branch
