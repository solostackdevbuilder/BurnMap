import glob
import json
from pathlib import Path

from paths import CLAUDE_PROJECTS_DIR, CLAUDE_XCODE_PROJECTS_DIR
from sources.base import ParseResult, UsageSource, normalize_session_id, project_name_from_cwd, update_session_record

DEFAULT_CLAUDE_DIRS = [CLAUDE_PROJECTS_DIR, CLAUDE_XCODE_PROJECTS_DIR]


def extract_assistant_turn(record, session_record):
    message = record.get("message", {})
    usage = message.get("usage", {})
    model = message.get("model", "")
    message_id = message.get("id", "")

    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    cache_creation = usage.get("cache_creation_input_tokens", 0) or 0

    if input_tokens + output_tokens + cache_read + cache_creation == 0:
        return None

    tool_names = [
        item.get("name")
        for item in message.get("content", [])
        if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name")
    ]
    tool_name = tool_names[0] if tool_names else None

    if model:
        session_record["model"] = model

    backend_provider = message.get("provider") or session_record.get("backend_provider") or "anthropic"

    return {
        "session_id": session_record["session_id"],
        "provider": session_record["provider"],
        "backend_provider": backend_provider,
        "client": session_record["client"],
        "timestamp": record.get("timestamp", ""),
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
        "native_cost": 0.0,
        "tool_call_count": len(tool_names),
        "tool_name": tool_name,
        "tool_names": ",".join(tool_names),
        "cwd": record.get("cwd", ""),
        "message_id": message_id,
        "parent_message_id": "",
    }


class ClaudeSource(UsageSource):
    provider = "claude"
    client = "claude-code"
    display_name = "Claude Code"

    def discover_files(self, projects_dir=None, projects_dirs=None):
        if projects_dirs:
            dirs_to_scan = [Path(d) for d in projects_dirs]
        elif projects_dir:
            dirs_to_scan = [Path(projects_dir)]
        else:
            dirs_to_scan = DEFAULT_CLAUDE_DIRS

        files = []
        for directory in dirs_to_scan:
            if directory.exists():
                files.extend(glob.glob(str(directory / "**" / "*.jsonl"), recursive=True))
        return sorted(files)

    def _build_session_record(self, record):
        raw_session_id = record.get("sessionId", "")
        return {
            "session_id": normalize_session_id(self.provider, raw_session_id),
            "provider": self.provider,
            "backend_provider": "anthropic",
            "client": self.client,
            "project_name": project_name_from_cwd(record.get("cwd", "")),
            "first_timestamp": record.get("timestamp", ""),
            "last_timestamp": record.get("timestamp", ""),
            "git_branch": record.get("gitBranch", ""),
            "model": None,
        }

    def parse_full_file(self, filepath):
        seen_messages = {}
        turns_without_id = []
        session_records = {}
        line_count = 0

        try:
            with open(filepath, encoding="utf-8", errors="replace") as handle:
                for line_count, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    record_type = record.get("type")
                    if record_type not in ("assistant", "user"):
                        continue

                    raw_session_id = record.get("sessionId")
                    if not raw_session_id:
                        continue

                    session_id = normalize_session_id(self.provider, raw_session_id)
                    if session_id not in session_records:
                        session_records[session_id] = self._build_session_record(record)
                    else:
                        update_session_record(session_records[session_id], record)

                    if record_type == "assistant":
                        turn = extract_assistant_turn(record, session_records[session_id])
                        if not turn:
                            continue
                        if turn["message_id"]:
                            seen_messages[turn["message_id"]] = turn
                        else:
                            turns_without_id.append(turn)
        except Exception as exc:
            print(f"  Warning: error reading {filepath}: {exc}")

        return ParseResult(list(session_records.values()), turns_without_id + list(seen_messages.values()), [], line_count)

    def parse_incremental_file(self, filepath, old_lines, skip_turn_ids=None):
        # Claude's incremental parse is line-offset-based; skip_turn_ids is
        # redundant here because already-persisted lines are skipped by the
        # old_lines guard. Accept and ignore for a uniform source signature.
        seen_messages = {}
        turns_without_id = []
        new_session_records = {}
        line_count = 0

        try:
            with open(filepath, encoding="utf-8", errors="replace") as handle:
                for line_count, line in enumerate(handle, 1):
                    if line_count <= old_lines:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    record_type = record.get("type")
                    if record_type not in ("assistant", "user"):
                        continue

                    raw_session_id = record.get("sessionId")
                    if not raw_session_id:
                        continue

                    session_id = normalize_session_id(self.provider, raw_session_id)
                    if session_id not in new_session_records:
                        new_session_records[session_id] = self._build_session_record(record)
                    else:
                        update_session_record(new_session_records[session_id], record)

                    if record_type == "assistant":
                        turn = extract_assistant_turn(record, new_session_records[session_id])
                        if not turn:
                            continue
                        if turn["message_id"]:
                            seen_messages[turn["message_id"]] = turn
                        else:
                            turns_without_id.append(turn)
        except Exception as exc:
            print(f"  Warning: {exc}")

        return ParseResult(list(new_session_records.values()), turns_without_id + list(seen_messages.values()), [], line_count)
