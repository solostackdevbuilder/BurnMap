from sources.base import UsageSource, normalize_session_id, project_name_from_cwd, update_session_record
from sources.claude import ClaudeSource, extract_assistant_turn
from sources.codex import CodexSource
from sources.opencode import OpenCodeSource
from sources.pi import PiSource, analyze_message_tree

ClaudeAdapter = ClaudeSource
CodexCliAdapter = CodexSource
PiAdapter = PiSource
OpenCodeAdapter = OpenCodeSource


def get_sources():
    return [ClaudeSource(), PiSource(), CodexSource(), OpenCodeSource()]


__all__ = [
    "UsageSource",
    "ClaudeSource",
    "CodexSource",
    "PiSource",
    "OpenCodeSource",
    "ClaudeAdapter",
    "CodexCliAdapter",
    "PiAdapter",
    "OpenCodeAdapter",
    "get_sources",
    "extract_assistant_turn",
    "normalize_session_id",
    "project_name_from_cwd",
    "update_session_record",
    "analyze_message_tree",
]
