import glob
import json
import os
from pathlib import Path

from paths import PI_SESSIONS_DIR
from sources.base import ParseResult, UsageSource, normalize_session_id, project_name_from_cwd


def iso_from_unix_ms(timestamp):
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def analyze_message_tree(entries):
    nodes = {}
    children = {}
    depths = {}

    for entry in entries:
        if entry.get("type") != "message":
            continue
        message_id = entry.get("id")
        if not message_id:
            continue
        parent_id = entry.get("parentId")
        nodes[message_id] = parent_id
        children.setdefault(message_id, [])

    edge_count = 0
    for message_id, parent_id in nodes.items():
        if parent_id and parent_id in nodes:
            children.setdefault(parent_id, []).append(message_id)
            edge_count += 1

    roots = [message_id for message_id, parent_id in nodes.items() if not parent_id or parent_id not in nodes]
    if not roots and nodes:
        roots = list(nodes.keys())

    max_depth = 0
    stack = [(root, 1) for root in roots]
    seen = set()
    while stack:
        node_id, depth = stack.pop()
        state_key = (node_id, depth)
        if state_key in seen:
            continue
        seen.add(state_key)
        depths[node_id] = max(depths.get(node_id, 0), depth)
        max_depth = max(max_depth, depth)
        for child_id in children.get(node_id, []):
            stack.append((child_id, depth + 1))

    branch_points = sum(1 for child_ids in children.values() if len(child_ids) > 1)
    leaf_count = sum(1 for message_id in nodes if not children.get(message_id))

    return {
        "tree_nodes": len(nodes),
        "tree_edges": edge_count,
        "tree_max_depth": max_depth,
        "tree_branch_points": branch_points,
        "tree_leaf_count": leaf_count,
        "tree_root_count": len(roots),
        "depths": depths,
    }


class PiSource(UsageSource):
    provider = "pi"
    client = "pi-agent"
    display_name = "Pi"

    def discover_files(self, projects_dir=None, projects_dirs=None):
        env_dir = Path(os.environ["PI_USAGE_DIR"]) if os.environ.get("PI_USAGE_DIR") else None
        candidates = [directory for directory in [env_dir, PI_SESSIONS_DIR] if directory]
        files = []
        for directory in candidates:
            if directory.exists():
                files.extend(glob.glob(str(directory / "**" / "*.jsonl"), recursive=True))
        return sorted(set(files))

    def _build_session_record(self, header, fallback_path):
        raw_session_id = header.get("id") or Path(fallback_path).stem
        cwd = header.get("cwd", "")
        return {
            "session_id": normalize_session_id(self.provider, raw_session_id),
            "provider": self.provider,
            "backend_provider": "",
            "client": self.client,
            "project_name": project_name_from_cwd(cwd),
            "first_timestamp": header.get("timestamp", ""),
            "last_timestamp": header.get("timestamp", ""),
            "git_branch": "",
            "model": None,
            "tree_nodes": 0,
            "tree_edges": 0,
            "tree_max_depth": 0,
            "tree_branch_points": 0,
            "tree_leaf_count": 0,
            "tree_root_count": 0,
        }

    def _parse_message_node(self, entry, session_record, depths=None):
        if entry.get("type") != "message":
            return None
        message = entry.get("message", {}) or {}
        message_id = entry.get("id", "")
        if not message_id:
            return None
        timestamp = message.get("timestamp")
        if isinstance(timestamp, (int, float)):
            ts = iso_from_unix_ms(timestamp)
        else:
            ts = entry.get("timestamp", "")
        role = message.get("role", "") or "unknown"
        provider = message.get("provider", "") or session_record.get("provider", "pi")
        model = message.get("model", "") or ""
        content = message.get("content")
        text_preview = ""
        tool_names = []
        if isinstance(content, str):
            text_preview = content[:160]
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and not text_preview:
                    text_preview = (item.get("text") or "")[:160]
                if item.get("type") in ("toolCall", "tool_use") and item.get("name"):
                    tool_names.append(item.get("name"))
        return {
            "session_id": session_record["session_id"],
            "message_id": message_id,
            "parent_message_id": entry.get("parentId", "") or "",
            "role": role,
            "timestamp": ts or entry.get("timestamp", ""),
            "provider": provider,
            "model": model,
            "tool_names": ",".join(tool_names),
            "text_preview": text_preview,
            "depth": (depths or {}).get(message_id, 0),
            "child_count": 0,
        }

    def _parse_assistant_turn(self, entry, session_record):
        message = entry.get("message", {})
        if entry.get("type") != "message" or message.get("role") != "assistant":
            return None
        usage = message.get("usage") or {}
        input_tokens = usage.get("input", 0) or 0
        output_tokens = usage.get("output", 0) or 0
        cache_read = usage.get("cacheRead", 0) or 0
        cache_creation = usage.get("cacheWrite", 0) or 0
        if input_tokens + output_tokens + cache_read + cache_creation == 0:
            return None

        native_cost = ((usage.get("cost") or {}).get("total", 0) or 0)
        provider = self.provider
        backend_provider = message.get("provider") or session_record.get("backend_provider") or ""
        if backend_provider and not session_record.get("backend_provider"):
            session_record["backend_provider"] = backend_provider
        model = message.get("model", "")
        if model:
            session_record["model"] = model
        timestamp = message.get("timestamp")
        if isinstance(timestamp, (int, float)):
            ts = iso_from_unix_ms(timestamp)
        else:
            ts = entry.get("timestamp", "")
        if ts:
            if not session_record["first_timestamp"] or ts < session_record["first_timestamp"]:
                session_record["first_timestamp"] = ts
            if not session_record["last_timestamp"] or ts > session_record["last_timestamp"]:
                session_record["last_timestamp"] = ts

        tool_names = [
            item.get("name")
            for item in message.get("content", [])
            if isinstance(item, dict) and item.get("type") == "toolCall" and item.get("name")
        ]
        tool_name = tool_names[0] if tool_names else None

        return {
            "session_id": session_record["session_id"],
            "provider": provider,
            "backend_provider": backend_provider,
            "client": self.client,
            "timestamp": ts or entry.get("timestamp", ""),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "native_cost": native_cost,
            "tool_call_count": len(tool_names),
            "tool_name": tool_name,
            "tool_names": ",".join(tool_names),
            "cwd": "",
            "message_id": entry.get("id", ""),
            "parent_message_id": entry.get("parentId", "") or "",
        }

    def _finalize_messages(self, message_entries, session_record):
        tree = analyze_message_tree(message_entries)
        session_record.update({key: value for key, value in tree.items() if key != "depths"})
        messages = [self._parse_message_node(entry, session_record, depths=tree.get("depths", {})) for entry in message_entries]
        messages = [message for message in messages if message]
        child_counts = {}
        for message in messages:
            parent_id = message.get("parent_message_id") or ""
            if parent_id:
                child_counts[parent_id] = child_counts.get(parent_id, 0) + 1
        for message in messages:
            message["child_count"] = child_counts.get(message["message_id"], 0)
        return messages

    def parse_full_file(self, filepath):
        turns = []
        session_record = None
        line_count = 0
        message_entries = []
        try:
            with open(filepath, encoding="utf-8", errors="replace") as handle:
                for line_count, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if session_record is None and entry.get("type") == "session":
                        session_record = self._build_session_record(entry, filepath)
                        continue
                    if session_record is None:
                        continue
                    if entry.get("type") == "message":
                        message_entries.append(entry)
                    turn = self._parse_assistant_turn(entry, session_record)
                    if turn:
                        turns.append(turn)
        except Exception as exc:
            print(f"  Warning: error reading {filepath}: {exc}")
        if session_record is None:
            return ParseResult([], [], [], line_count)
        messages = self._finalize_messages(message_entries, session_record)
        return ParseResult([session_record], turns, messages, line_count)

    def parse_incremental_file(self, filepath, old_lines, skip_turn_ids=None):
        turns = []
        session_record = None
        line_count = 0
        message_entries = []
        try:
            with open(filepath, encoding="utf-8", errors="replace") as handle:
                for line_count, line in enumerate(handle, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if session_record is None and entry.get("type") == "session":
                        session_record = self._build_session_record(entry, filepath)
                    if session_record is None:
                        continue
                    if entry.get("type") == "message":
                        message_entries.append(entry)
                    if line_count <= old_lines:
                        continue
                    turn = self._parse_assistant_turn(entry, session_record)
                    if turn:
                        turns.append(turn)
        except Exception as exc:
            print(f"  Warning: {exc}")
        if session_record is None:
            return ParseResult([], [], [], line_count)
        messages = self._finalize_messages(message_entries, session_record)
        return ParseResult([session_record], turns, messages, line_count)
