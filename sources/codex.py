import glob
import json
import os
from pathlib import Path

from paths import CODEX_SESSIONS_DIR
from sources.base import ParseResult, UsageSource, normalize_session_id, project_name_from_cwd, update_session_record


def count_lines(filepath):
    try:
        with open(filepath, encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


_TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _add_tokens(accumulator, usage):
    for key in _TOKEN_KEYS:
        value = usage.get(key) if isinstance(usage, dict) else None
        if isinstance(value, (int, float)) and value > 0:
            accumulator[key] += int(value)


class CodexSource(UsageSource):
    provider = "codex"
    client = "codex"
    display_name = "Codex"

    def discover_files(self, projects_dir=None, projects_dirs=None):
        env_dir = Path(os.environ["CODEX_USAGE_DIR"]) if os.environ.get("CODEX_USAGE_DIR") else None
        candidates = [directory for directory in [env_dir, CODEX_SESSIONS_DIR] if directory]
        files = []
        for directory in candidates:
            if directory.exists():
                files.extend(glob.glob(str(directory / "**" / "*.jsonl"), recursive=True))
        return sorted(files)

    def _parse(self, filepath, skip_turn_ids=None, skip_up_to_line=0):
        """
        Walk a Codex rollout JSONL and reconstruct sessions + turns.

        Codex emits:
          - session_meta: one per file (id, cwd, timestamp, model_provider)
          - turn_context: before each turn, carries turn_id + model
          - event_msg task_started / task_complete: bracket a turn (turn_id)
          - event_msg token_count: cumulative token usage at points in the session
                info.total_token_usage carries session-wide running totals
          - event_msg agent_message: assistant output (useful for timestamp bookends)
          - response_item function_call: tool calls within a turn

        Turn tokens are the delta in total_token_usage between the start and
        end of a turn. Tool calls inside a turn are counted.
        """
        session_records = {}
        turns = []
        session_id = None
        line_count = 0

        current_turn_tokens = {key: 0 for key in _TOKEN_KEYS}
        current_turn_id = None
        current_turn_model = None
        current_turn_started_at = None
        current_turn_cwd = ""
        current_turn_tool_calls = []
        turn_models = {}

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

                    rtype = record.get("type")
                    payload = record.get("payload") or {}
                    timestamp = record.get("timestamp") or payload.get("timestamp") or ""

                    if rtype == "session_meta":
                        raw_id = payload.get("id", "")
                        if not raw_id:
                            continue
                        session_id = normalize_session_id(self.provider, raw_id)
                        session_records[session_id] = {
                            "session_id": session_id,
                            "provider": self.provider,
                            "backend_provider": payload.get("model_provider") or "openai",
                            "client": self.client,
                            "project_name": project_name_from_cwd(payload.get("cwd", "")),
                            "first_timestamp": payload.get("timestamp") or timestamp,
                            "last_timestamp": payload.get("timestamp") or timestamp,
                            "git_branch": "",
                            "model": None,
                        }
                        continue

                    if session_id is None:
                        # Pre-session-meta events are ignored (malformed file).
                        continue

                    if timestamp:
                        update_session_record(session_records[session_id], {"timestamp": timestamp})

                    if rtype == "turn_context":
                        tid = payload.get("turn_id")
                        model = payload.get("model")
                        if tid and model:
                            turn_models[tid] = model
                        if payload.get("cwd"):
                            current_turn_cwd = payload["cwd"]
                        continue

                    if rtype == "event_msg":
                        ptype = payload.get("type")

                        if ptype == "task_started":
                            current_turn_id = payload.get("turn_id")
                            current_turn_started_at = timestamp
                            current_turn_tool_calls = []
                            current_turn_tokens = {key: 0 for key in _TOKEN_KEYS}
                            current_turn_model = turn_models.get(current_turn_id) or current_turn_model
                            continue

                        if ptype == "token_count":
                            # last_token_usage is per-API-call and is additive within a turn
                            # (Codex may make multiple model calls per task). Sum them instead
                            # of diffing total_token_usage, which resets across context compaction.
                            if current_turn_id is None:
                                continue
                            info = payload.get("info")
                            if isinstance(info, dict):
                                _add_tokens(current_turn_tokens, info.get("last_token_usage") or {})
                            continue

                        if ptype == "task_complete":
                            tid = payload.get("turn_id") or current_turn_id
                            if not tid:
                                continue
                            # Incremental scan skip: if this task_complete is
                            # in a region the DB already persisted, or matches
                            # a known persisted turn_id, reset state and move on.
                            if line_count <= skip_up_to_line or (skip_turn_ids and tid in skip_turn_ids):
                                current_turn_id = None
                                current_turn_tokens = {key: 0 for key in _TOKEN_KEYS}
                                current_turn_tool_calls = []
                                continue
                            input_total = current_turn_tokens["input_tokens"]
                            cache_read = current_turn_tokens["cached_input_tokens"]
                            non_cached_input = max(0, input_total - cache_read)
                            output = current_turn_tokens["output_tokens"] + current_turn_tokens["reasoning_output_tokens"]
                            if non_cached_input + cache_read + output == 0:
                                # No tokens recorded for this turn (e.g. aborted). Skip.
                                current_turn_id = None
                                continue
                            model = turn_models.get(tid) or current_turn_model or (session_records[session_id].get("model") or "")
                            if model:
                                session_records[session_id]["model"] = model
                            tool_names = [name for name in current_turn_tool_calls if name]
                            turns.append({
                                "session_id": session_id,
                                "provider": self.provider,
                                "backend_provider": session_records[session_id].get("backend_provider") or "openai",
                                "client": self.client,
                                "timestamp": timestamp or current_turn_started_at or "",
                                "model": model,
                                "input_tokens": non_cached_input,
                                "output_tokens": output,
                                "cache_read_tokens": cache_read,
                                "cache_creation_tokens": 0,
                                "native_cost": 0.0,
                                "tool_call_count": len(tool_names),
                                "tool_name": tool_names[0] if tool_names else None,
                                "tool_names": ",".join(tool_names),
                                "cwd": current_turn_cwd,
                                "message_id": tid,
                                "parent_message_id": "",
                            })
                            current_turn_id = None
                            continue

                        continue

                    if rtype == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                        name = payload.get("name") or payload.get("tool_name")
                        if name and current_turn_id is not None:
                            current_turn_tool_calls.append(name)
                        continue
        except Exception as exc:
            print(f"  Warning: error reading {filepath}: {exc}")

        return ParseResult(list(session_records.values()), turns, [], line_count)

    def parse_full_file(self, filepath):
        return self._parse(filepath)

    def parse_incremental_file(self, filepath, old_lines, skip_turn_ids=None):
        # Codex turns span multiple lines (task_started ... token_count ... task_complete),
        # so we can't just seek past old_lines and start there — per-turn tokens depend
        # on state (current_turn_tokens) that we build up across the whole session.
        #
        # We DO re-walk the file (disk IO is the floor), but we track state silently
        # for lines <= old_lines and only emit turns once task_complete lands past that
        # boundary. skip_turn_ids is a safety belt for session-id-level re-dedup in
        # case old_lines was reset (e.g. after a DB wipe).
        return self._parse(filepath, skip_turn_ids=skip_turn_ids, skip_up_to_line=old_lines)
