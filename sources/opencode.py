import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from paths import OPENCODE_DB_PATH
from sources.base import ParseResult, UsageSource, normalize_session_id, project_name_from_cwd


WSL_WINDOWS_USERS_DIR = Path("/mnt/c/Users")


def iso_from_unix_ms(timestamp):
    try:
        return datetime.fromtimestamp(int(timestamp) / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def _load_json(raw):
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _clamp_int(value):
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _derive_backend_provider(message, parts):
    provider_id = (message.get("providerID") or "").strip().lower()
    if provider_id and provider_id != "opencode":
        return provider_id

    preferred = ("anthropic", "openai", "openrouter", "google", "xai", "groq", "deepseek", "mistral")
    seen = set()
    for part in parts:
        metadata = part.get("metadata") or {}
        for key in metadata:
            normalized = (key or "").strip().lower()
            if normalized:
                seen.add(normalized)
    for candidate in preferred:
        if candidate in seen:
            return candidate

    model = (message.get("modelID") or "").strip().lower()
    if model.startswith("openai/") or model.startswith("gpt-"):
        return "openai"
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini"):
        return "google"
    if model.startswith("grok"):
        return "xai"
    if model.startswith("deepseek"):
        return "deepseek"
    if model.startswith("mistral"):
        return "mistral"
    return ""


class OpenCodeSource(UsageSource):
    provider = "opencode"
    client = "opencode"
    display_name = "OpenCode"

    def _candidate_paths(self):
        env_path = Path(os.environ["OPENCODE_DB_PATH"]) if os.environ.get("OPENCODE_DB_PATH") else None
        candidates = [path for path in [env_path, OPENCODE_DB_PATH] if path]
        if WSL_WINDOWS_USERS_DIR.exists():
            try:
                for child in WSL_WINDOWS_USERS_DIR.iterdir():
                    candidates.append(child / ".local" / "share" / "opencode" / "opencode.db")
            except OSError:
                pass
        return candidates

    def discover_files(self, projects_dir=None, projects_dirs=None):
        files = []
        for candidate in self._candidate_paths():
            try:
                if candidate.exists() and candidate.is_file():
                    files.append(str(candidate))
            except OSError:
                continue
        return sorted(set(files))

    def _load_candidate_messages(self, conn, since_cursor=None):
        query = """
            SELECT
                m.id AS message_id,
                m.session_id AS raw_session_id,
                m.time_created AS message_time_created,
                m.time_updated AS message_time_updated,
                COALESCE(m.time_updated, m.time_created, 0) AS cursor,
                m.data AS message_data,
                s.directory AS session_directory,
                s.project_id AS project_id,
                s.time_created AS session_time_created,
                s.time_updated AS session_time_updated,
                p.worktree AS project_worktree
            FROM message m
            JOIN session s ON s.id = m.session_id
            LEFT JOIN project p ON p.id = s.project_id
        """
        params = []
        if since_cursor is not None:
            query += " WHERE COALESCE(m.time_updated, m.time_created, 0) > ?"
            params.append(int(since_cursor))
        query += " ORDER BY COALESCE(m.time_updated, m.time_created, 0), m.id"
        return conn.execute(query, params).fetchall()

    def _load_parts_by_message(self, conn, message_ids):
        by_message = defaultdict(list)
        if not message_ids:
            return by_message
        batch_size = 250
        for start in range(0, len(message_ids), batch_size):
            batch = message_ids[start:start + batch_size]
            placeholders = ",".join("?" for _ in batch)
            query = f"SELECT message_id, data FROM part WHERE message_id IN ({placeholders}) ORDER BY time_created, id"
            for message_id, data in conn.execute(query, batch):
                by_message[message_id].append(_load_json(data))
        return by_message

    def _build_session_record(self, row, message, backend_provider):
        project_path = row["project_worktree"] or row["session_directory"] or ((message.get("path") or {}).get("cwd") or "")
        project_name = project_name_from_cwd(project_path)
        first_timestamp = iso_from_unix_ms(row["session_time_created"]) or iso_from_unix_ms(row["message_time_created"]) or ""
        last_timestamp = iso_from_unix_ms(row["session_time_updated"]) or iso_from_unix_ms(row["message_time_updated"]) or first_timestamp
        return {
            "session_id": normalize_session_id(self.provider, row["raw_session_id"]),
            "provider": self.provider,
            "backend_provider": backend_provider,
            "client": self.client,
            "project_name": project_name,
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "git_branch": "",
            "model": message.get("modelID") or None,
        }

    def _build_turn(self, row, message, parts):
        if message.get("role") != "assistant":
            return None

        time_info = message.get("time") or {}
        if not time_info.get("completed") and not message.get("error"):
            return None

        step_finish = None
        for part in reversed(parts):
            if part.get("type") == "step-finish":
                step_finish = part
                break

        tokens = message.get("tokens") or (step_finish or {}).get("tokens") or {}
        cache = tokens.get("cache") or {}
        input_tokens = _clamp_int(tokens.get("input"))
        output_tokens = _clamp_int((tokens.get("output") or 0) + (tokens.get("reasoning") or 0))
        cache_read = _clamp_int(cache.get("read"))
        cache_creation = _clamp_int(cache.get("write"))
        native_cost = float(message.get("cost") or (step_finish or {}).get("cost") or 0.0)

        if input_tokens + output_tokens + cache_read + cache_creation == 0 and native_cost <= 0:
            return None

        tool_names = [
            part.get("tool")
            for part in parts
            if part.get("type") == "tool" and part.get("tool")
        ]
        backend_provider = _derive_backend_provider(message, parts)
        cwd = ((message.get("path") or {}).get("cwd") or row["session_directory"] or row["project_worktree"] or "")
        completed_at = time_info.get("completed") or row["message_time_updated"] or row["message_time_created"]

        return {
            "session_id": normalize_session_id(self.provider, row["raw_session_id"]),
            "provider": self.provider,
            "backend_provider": backend_provider,
            "client": self.client,
            "timestamp": iso_from_unix_ms(completed_at) or iso_from_unix_ms(row["message_time_created"]) or "",
            "model": message.get("modelID") or "",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "native_cost": native_cost,
            "tool_call_count": len(tool_names),
            "tool_name": tool_names[0] if tool_names else None,
            "tool_names": ",".join(tool_names),
            "cwd": cwd,
            "message_id": row["message_id"],
            "parent_message_id": message.get("parentID") or "",
        }

    def _parse(self, filepath, since_cursor=None):
        session_records = {}
        turns = []
        line_count = int(since_cursor or 0)

        try:
            conn = sqlite3.connect(filepath)
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            print(f"  Warning: error opening {filepath}: {exc}")
            return ParseResult([], [], [], line_count)

        try:
            rows = self._load_candidate_messages(conn, since_cursor=since_cursor)
            if rows:
                line_count = max(line_count, max(int(row["cursor"] or 0) for row in rows))
            assistant_message_ids = [row["message_id"] for row in rows if (_load_json(row["message_data"]).get("role") == "assistant")]
            parts_by_message = self._load_parts_by_message(conn, assistant_message_ids)

            for row in rows:
                message = _load_json(row["message_data"])
                session_id = normalize_session_id(self.provider, row["raw_session_id"])
                row_timestamp = iso_from_unix_ms(row["message_time_updated"]) or iso_from_unix_ms(row["message_time_created"]) or ""
                if session_id not in session_records:
                    session_records[session_id] = self._build_session_record(row, message, "")
                elif row_timestamp and row_timestamp > session_records[session_id]["last_timestamp"]:
                    session_records[session_id]["last_timestamp"] = row_timestamp

                if message.get("role") != "assistant":
                    continue
                turn = self._build_turn(row, message, parts_by_message.get(row["message_id"], []))
                if not turn:
                    continue
                if turn["timestamp"] and turn["timestamp"] > session_records[session_id]["last_timestamp"]:
                    session_records[session_id]["last_timestamp"] = turn["timestamp"]
                if turn.get("model"):
                    session_records[session_id]["model"] = turn["model"]
                if turn.get("backend_provider"):
                    session_records[session_id]["backend_provider"] = turn["backend_provider"]
                turns.append(turn)
        except Exception as exc:
            print(f"  Warning: error reading {filepath}: {exc}")
        finally:
            conn.close()

        return ParseResult(list(session_records.values()), turns, [], line_count)

    def parse_full_file(self, filepath):
        return self._parse(filepath)

    def parse_incremental_file(self, filepath, old_lines, skip_turn_ids=None):
        # OpenCode stores mutable session state in SQLite rather than append-only
        # JSONL files. We therefore treat processed_files.lines as a high-water
        # mark of message.time_updated (ms since epoch) and fetch only assistant
        # messages updated after that cursor.
        return self._parse(filepath, since_cursor=old_lines or 0)
