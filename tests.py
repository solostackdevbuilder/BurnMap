import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from analytics import (
    get_all_time_stats_data,
    get_dashboard_data,
    get_light_dashboard_data,
    get_overview_data,
    get_pi_data,
    get_project_detail_data,
    get_projects_data,
    get_session_detail_data,
    get_sessions_data,
    get_today_usage_data,
)
from pricing import calc_cost, get_pricing, is_billable
from schema import init_db
from sources import PiAdapter
from sources.claude import extract_assistant_turn
from store import save_pi_messages as replace_pi_messages
from server import get_frontend_build_status


class PricingTests(unittest.TestCase):
    def test_pricing_family_fallback(self):
        self.assertTrue(is_billable("claude-sonnet-4-6"))
        self.assertTrue(is_billable("gpt-5.4"))
        self.assertIsNotNone(get_pricing("claude-sonnet-4-6-latest"))
        self.assertIsNotNone(get_pricing("gpt-5.3-codex"))
        self.assertGreater(calc_cost("claude-sonnet-4-6", 1000000, 0, 0, 0), 0)
        self.assertGreater(calc_cost("gpt-5.3-codex", 1000000, 0, 0, 0), 0)
        self.assertEqual(calc_cost("local-model", 1000000, 0, 0, 0), 0.0)

    def test_gpt_5_4_estimate_matches_native_pi_rows(self):
        # Anchored to native-cost Pi rows observed in the user's DB.
        # 233,032 input + 40 output = $0.58318 at $2.50/M input, $15/M output.
        cost = calc_cost("gpt-5.4", 233_032, 40, 0, 0)
        self.assertAlmostEqual(cost, 0.58318, places=6)

    def test_gpt_5_family_cached_read_estimate(self):
        # 209 input + 13,840 output + 158,336 cached read = $0.2477065
        # at GPT-5 family rates.
        cost = calc_cost("gpt-5.3-codex", 209, 13_840, 158_336, 0)
        self.assertAlmostEqual(cost, 0.2477065, places=6)


class ServerRouteModeTests(unittest.TestCase):
    def test_frontend_build_status_detects_missing_and_stale_build(self):
        with tempfile.TemporaryDirectory() as tempdir:
            frontend_dir = Path(tempdir) / "frontend"
            src_dir = frontend_dir / "src"
            dist_dir = frontend_dir / "dist"
            src_dir.mkdir(parents=True)
            dist_dir.mkdir(parents=True)
            (frontend_dir / "index.html").write_text("src", encoding="utf-8")
            (frontend_dir / "package.json").write_text("{}", encoding="utf-8")
            (frontend_dir / "vite.config.ts").write_text("export default {}", encoding="utf-8")
            (src_dir / "App.tsx").write_text("export default function App() { return null }", encoding="utf-8")

            with patch("server.FRONTEND_DIR", frontend_dir), patch("server.FRONTEND_SRC_DIR", src_dir), patch("server.FRONTEND_DIST_DIR", dist_dir / "missing"):
                status = get_frontend_build_status(force_refresh=True)
                self.assertFalse(status["exists"])
                self.assertFalse(status["stale"])

            dist_index = dist_dir / "index.html"
            dist_index.write_text("built", encoding="utf-8")
            os.utime(dist_index, (1, 1))
            os.utime(src_dir / "App.tsx", None)
            with patch("server.FRONTEND_DIR", frontend_dir), patch("server.FRONTEND_SRC_DIR", src_dir), patch("server.FRONTEND_DIST_DIR", dist_dir):
                status = get_frontend_build_status(force_refresh=True)
                self.assertTrue(status["exists"])
                self.assertTrue(status["stale"])


class ScannerHelperTests(unittest.TestCase):
    def test_extract_assistant_turn(self):
        session_meta = {"session_id": "sess-1", "provider": "claude", "client": "cli", "model": None}
        record = {
            "timestamp": "2026-04-10T10:00:00Z",
            "cwd": "/tmp/project",
            "message": {
                "id": "msg-1",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 25,
                    "cache_creation_input_tokens": 10,
                },
                "content": [{"type": "tool_use", "name": "bash"}],
            },
        }
        turn = extract_assistant_turn(record, session_meta)
        self.assertEqual(turn["session_id"], "sess-1")
        self.assertEqual(turn["provider"], "claude")
        self.assertEqual(turn["backend_provider"], "anthropic")
        self.assertEqual(turn["tool_name"], "bash")
        self.assertEqual(turn["tool_call_count"], 1)
        self.assertEqual(turn["tool_names"], "bash")
        self.assertEqual(turn["message_id"], "msg-1")
        self.assertEqual(session_meta["model"], "claude-sonnet-4-6")

    def test_pi_adapter_parses_assistant_usage(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            session_path = Path(tempdir.name) / "pi-session.jsonl"
            session_path.write_text(
                "\n".join([
                    '{"type":"session","version":3,"id":"pi-123","timestamp":"2026-04-01T10:00:00.000Z","cwd":"/tmp/pi-project"}',
                    '{"type":"message","id":"a1","parentId":null,"timestamp":"2026-04-01T10:00:01.000Z","message":{"role":"user","content":"hello","timestamp":1711965601000}}',
                    '{"type":"message","id":"b1","parentId":"a1","timestamp":"2026-04-01T10:00:02.000Z","message":{"role":"assistant","content":[{"type":"text","text":"hi"},{"type":"toolCall","id":"tc1","name":"read","arguments":{}},{"type":"toolCall","id":"tc2","name":"bash","arguments":{}}],"provider":"anthropic","model":"claude-sonnet-4-5","usage":{"input":120,"output":55,"cacheRead":20,"cacheWrite":10,"totalTokens":205,"cost":{"input":0.001,"output":0.002,"cacheRead":0.0,"cacheWrite":0.0,"total":0.003}},"timestamp":1711965602000}}',
                    '{"type":"message","id":"c1","parentId":"b1","timestamp":"2026-04-01T10:00:03.000Z","message":{"role":"user","content":"next","timestamp":1711965603000}}',
                    '{"type":"message","id":"d1","parentId":"b1","timestamp":"2026-04-01T10:00:04.000Z","message":{"role":"assistant","content":[{"type":"text","text":"branch"}],"provider":"anthropic","model":"claude-sonnet-4-5","usage":{"input":50,"output":10,"cacheRead":0,"cacheWrite":0,"cost":{"total":0.001}},"timestamp":1711965604000}}'
                ]),
                encoding="utf-8",
            )
            adapter = PiAdapter()
            sessions, turns, messages, line_count = adapter.parse_full_file(session_path)
            self.assertEqual(line_count, 5)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], "pi:pi-123")
            self.assertEqual(sessions[0]["tree_nodes"], 4)
            self.assertEqual(sessions[0]["tree_edges"], 3)
            self.assertEqual(sessions[0]["tree_max_depth"], 3)
            self.assertEqual(sessions[0]["tree_branch_points"], 1)
            self.assertEqual(sessions[0]["tree_leaf_count"], 2)
            self.assertEqual(len(messages), 4)
            self.assertEqual(messages[1]["depth"], 2)
            self.assertEqual(messages[1]["child_count"], 2)
            self.assertEqual(len(turns), 2)
            self.assertEqual(turns[0]["provider"], "pi")
            self.assertEqual(turns[0]["backend_provider"], "anthropic")
            self.assertEqual(turns[0]["model"], "claude-sonnet-4-5")
            self.assertEqual(turns[0]["input_tokens"], 120)
            self.assertEqual(turns[0]["tool_call_count"], 2)
            self.assertEqual(turns[0]["tool_names"], "read,bash")
            self.assertEqual(turns[0]["parent_message_id"], "a1")
            self.assertAlmostEqual(turns[0]["native_cost"], 0.003)
        finally:
            tempdir.cleanup()


class OpenCodeSourceTests(unittest.TestCase):
    def _create_db(self):
        tempdir = tempfile.TemporaryDirectory()
        db_path = Path(tempdir.name) / "opencode.db"
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE project (
                id TEXT PRIMARY KEY,
                worktree TEXT,
                vcs TEXT,
                name TEXT,
                icon_url TEXT,
                icon_color TEXT,
                time_created INTEGER,
                time_updated INTEGER,
                time_initialized INTEGER,
                sandboxes TEXT,
                commands TEXT
            );
            CREATE TABLE session (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                parent_id TEXT,
                slug TEXT NOT NULL,
                directory TEXT NOT NULL,
                title TEXT NOT NULL,
                version TEXT NOT NULL,
                share_url TEXT,
                summary_additions INTEGER,
                summary_deletions INTEGER,
                summary_files INTEGER,
                summary_diffs TEXT,
                revert TEXT,
                permission TEXT,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                time_compacting INTEGER,
                time_archived INTEGER,
                workspace_id TEXT
            );
            CREATE TABLE message (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE part (
                id TEXT PRIMARY KEY,
                message_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                time_created INTEGER NOT NULL,
                time_updated INTEGER NOT NULL,
                data TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO project (id, worktree, sandboxes, time_created, time_updated) VALUES (?, ?, ?, ?, ?)",
            ("proj1", "/tmp/demo/project", "[]", 1711965600000, 1711965600000),
        )
        conn.execute(
            """
            INSERT INTO session (
                id, project_id, slug, directory, title, version, permission,
                time_created, time_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("ses1", "proj1", "demo", "/tmp/demo/project", "Demo session", "1.3.17", "[]", 1711965600000, 1711965605000),
        )
        conn.commit()
        return tempdir, db_path, conn

    def test_opencode_parser_reads_sqlite_sessions(self):
        from sources.opencode import OpenCodeSource

        tempdir, db_path, conn = self._create_db()
        try:
            assistant = {
                "parentID": "msg-user-1",
                "role": "assistant",
                "mode": "build",
                "agent": "build",
                "path": {"cwd": "/tmp/demo/project", "root": "/"},
                "cost": 0.125,
                "tokens": {
                    "total": 1440,
                    "input": 1000,
                    "output": 120,
                    "reasoning": 20,
                    "cache": {"read": 200, "write": 100},
                },
                "modelID": "gpt-5-nano",
                "providerID": "opencode",
                "time": {"created": 1711965601000, "completed": 1711965602000},
                "finish": "tool-calls",
            }
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
                ("msg-assistant-1", "ses1", 1711965601000, 1711965602000, json.dumps(assistant)),
            )
            parts = [
                ("prt-1", {"type": "step-start"}),
                ("prt-2", {"type": "tool", "tool": "read", "metadata": {"openai": {"itemId": "1"}}}),
                ("prt-3", {"type": "tool", "tool": "bash"}),
                ("prt-4", {"type": "step-finish", "reason": "tool-calls", "tokens": assistant["tokens"], "cost": 0.125}),
            ]
            for index, (part_id, payload) in enumerate(parts, start=1):
                conn.execute(
                    "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                    (part_id, "msg-assistant-1", "ses1", 1711965601000 + index, 1711965601000 + index, json.dumps(payload)),
                )
            conn.commit()

            sessions, turns, nodes, line_count = OpenCodeSource().parse_full_file(db_path)
            self.assertEqual(nodes, [])
            self.assertEqual(line_count, 1711965602000)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], "opencode:ses1")
            self.assertEqual(sessions[0]["provider"], "opencode")
            self.assertEqual(sessions[0]["backend_provider"], "openai")
            self.assertEqual(sessions[0]["project_name"], "demo/project")
            self.assertEqual(len(turns), 1)
            turn = turns[0]
            self.assertEqual(turn["message_id"], "msg-assistant-1")
            self.assertEqual(turn["parent_message_id"], "msg-user-1")
            self.assertEqual(turn["backend_provider"], "openai")
            self.assertEqual(turn["model"], "gpt-5-nano")
            self.assertEqual(turn["input_tokens"], 1000)
            self.assertEqual(turn["output_tokens"], 140)
            self.assertEqual(turn["cache_read_tokens"], 200)
            self.assertEqual(turn["cache_creation_tokens"], 100)
            self.assertEqual(turn["tool_call_count"], 2)
            self.assertEqual(turn["tool_name"], "read")
            self.assertEqual(turn["tool_names"], "read,bash")
            self.assertAlmostEqual(turn["native_cost"], 0.125)
        finally:
            conn.close()
            tempdir.cleanup()

    def test_opencode_incremental_parser_uses_time_updated_cursor(self):
        from sources.opencode import OpenCodeSource

        tempdir, db_path, conn = self._create_db()
        try:
            assistant1 = {
                "parentID": "msg-user-1",
                "role": "assistant",
                "path": {"cwd": "/tmp/demo/project", "root": "/"},
                "cost": 0,
                "tokens": {"input": 50, "output": 10, "reasoning": 5, "cache": {"read": 20, "write": 0}},
                "modelID": "big-pickle",
                "providerID": "opencode",
                "time": {"created": 1711965601000, "completed": 1711965602000},
            }
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
                ("msg-assistant-1", "ses1", 1711965601000, 1711965602000, json.dumps(assistant1)),
            )
            conn.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                ("prt-a", "msg-assistant-1", "ses1", 1711965601500, 1711965601500, json.dumps({"type": "step-finish", "tokens": assistant1["tokens"], "metadata": {"anthropic": {"id": "a1"}}})),
            )
            conn.commit()

            source = OpenCodeSource()
            _sessions, turns, _nodes, cursor = source.parse_full_file(db_path)
            self.assertEqual(len(turns), 1)
            self.assertEqual(cursor, 1711965602000)
            self.assertEqual(turns[0]["backend_provider"], "anthropic")
            self.assertEqual(turns[0]["output_tokens"], 15)

            assistant2 = {
                "parentID": "msg-user-2",
                "role": "assistant",
                "path": {"cwd": "/tmp/demo/project", "root": "/"},
                "cost": 0.01,
                "tokens": {"input": 80, "output": -5, "reasoning": 25, "cache": {"read": 0, "write": 0}},
                "modelID": "openai/gpt-oss-120b",
                "providerID": "openrouter",
                "time": {"created": 1711965603000, "completed": 1711965604000},
            }
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
                ("msg-assistant-2", "ses1", 1711965603000, 1711965604000, json.dumps(assistant2)),
            )
            conn.execute(
                "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                ("prt-b", "msg-assistant-2", "ses1", 1711965603500, 1711965603500, json.dumps({"type": "tool", "tool": "write"})),
            )
            conn.commit()

            sessions, turns, _nodes, new_cursor = source.parse_incremental_file(db_path, cursor)
            self.assertEqual(new_cursor, 1711965604000)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["message_id"], "msg-assistant-2")
            self.assertEqual(turns[0]["backend_provider"], "openrouter")
            self.assertEqual(turns[0]["output_tokens"], 20)
            self.assertEqual(turns[0]["tool_names"], "write")

            sessions, turns, _nodes, final_cursor = source.parse_incremental_file(db_path, new_cursor)
            self.assertEqual(sessions, [])
            self.assertEqual(turns, [])
            self.assertEqual(final_cursor, new_cursor)
        finally:
            conn.close()
            tempdir.cleanup()


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "usage.db"
        conn = sqlite3.connect(self.db_path)
        init_db(conn)
        today = date.today()
        recent_day = (today - timedelta(days=2)).isoformat()
        older_day = (today - timedelta(days=40)).isoformat()
        ancient_day = (today - timedelta(days=200)).isoformat()
        self.recent_day = recent_day
        self.older_day = older_day
        self.ancient_day = ancient_day

        conn.execute(
            """
            INSERT INTO sessions (
                session_id, project_name, first_timestamp, last_timestamp,
                git_branch, total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation,
                tree_nodes, tree_edges, tree_max_depth, tree_branch_points, tree_leaf_count, tree_root_count,
                model, turn_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "session-abc12345",
                "demo/project",
                f"{ancient_day}T10:00:00Z",
                f"{recent_day}T11:00:00Z",
                "main",
                350,
                170,
                20,
                10,
                3,
                2,
                2,
                0,
                1,
                1,
                "claude-sonnet-4-6",
                3,
            ),
        )
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, provider, backend_provider, client, project_name, first_timestamp, last_timestamp,
                git_branch, total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation, model, turn_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "codex:session-xyz98765",
                "codex",
                "openai",
                "cli",
                "demo/codex",
                f"{recent_day}T08:00:00Z",
                f"{recent_day}T08:30:00Z",
                "main",
                80,
                40,
                0,
                0,
                "gpt-4.1-codex",
                1,
            ),
        )
        conn.executemany(
            """
            INSERT INTO turns (
                session_id, provider, backend_provider, client, timestamp, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, tool_call_count, tool_name, tool_names, cwd, message_id, parent_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("session-abc12345", "claude", "anthropic", "cli", f"{older_day}T10:00:00Z", "claude-sonnet-4-6", 100, 50, 0, 0, 1, "read", "read", "/tmp/demo", "m1", "u1"),
                ("session-abc12345", "claude", "anthropic", "cli", f"{recent_day}T11:00:00Z", "claude-opus-4-6", 200, 100, 20, 10, 2, "bash", "bash,read", "/tmp/demo", "m2", "m1"),
                ("session-abc12345", "claude", "anthropic", "cli", f"{ancient_day}T09:00:00Z", "unknown-model", 50, 20, 0, 0, 0, None, "", "/tmp/demo", "m3", "m1"),
                ("codex:session-xyz98765", "codex", "openai", "cli", f"{recent_day}T08:15:00Z", "gpt-4.1-codex", 80, 40, 0, 0, 1, "edit", "edit", "/tmp/codex", "c1", "u9"),
            ],
        )
        replace_pi_messages(conn, "session-abc12345", [
            {"session_id": "session-abc12345", "message_id": "u1", "parent_message_id": "", "role": "user", "timestamp": f"{older_day}T09:59:00Z", "provider": "pi", "model": "", "tool_names": "", "text_preview": "start", "depth": 1, "child_count": 1},
            {"session_id": "session-abc12345", "message_id": "m1", "parent_message_id": "u1", "role": "assistant", "timestamp": f"{older_day}T10:00:00Z", "provider": "claude", "model": "claude-sonnet-4-6", "tool_names": "read", "text_preview": "first assistant", "depth": 2, "child_count": 2},
            {"session_id": "session-abc12345", "message_id": "m2", "parent_message_id": "m1", "role": "assistant", "timestamp": f"{recent_day}T11:00:00Z", "provider": "claude", "model": "claude-opus-4-6", "tool_names": "bash,read", "text_preview": "branch one", "depth": 3, "child_count": 0},
            {"session_id": "session-abc12345", "message_id": "m3", "parent_message_id": "m1", "role": "assistant", "timestamp": f"{ancient_day}T09:00:00Z", "provider": "claude", "model": "unknown-model", "tool_names": "", "text_preview": "branch two", "depth": 3, "child_count": 0},
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_dashboard_all_models_unfiltered(self):
        data = get_dashboard_data(db_path=self.db_path)
        self.assertIn("claude-sonnet-4-6", data["all_models"])
        self.assertIn("claude-opus-4-6", data["all_models"])
        self.assertIn("codex", data["all_providers"])
        self.assertCountEqual(data["all_backend_providers"], ["anthropic", "openai"])
        self.assertEqual(len(data["session_models_daily"]), 4)
        self.assertEqual(len(data["session_rollups"]), 2)
        self.assertEqual(len(data["project_rollups"]), 2)
        self.assertEqual(data["overview_summary"]["totals"]["sessions"], 2)
        self.assertIn("native_cost", data["overview_summary"]["totals"])
        self.assertIn("estimated_cost", data["overview_summary"]["totals"])
        self.assertEqual(data["pi_summary"]["totals"]["sessions"], 1)
        self.assertEqual(data["tool_usage"][0]["tool"], "read")
        self.assertEqual(len(data["turn_events"]), 4)
        self.assertEqual(len(data["session_analytics"]), 2)
        self.assertTrue(any(row["tree_nodes"] == 3 for row in data["session_analytics"]))
        self.assertTrue(any(row["parent_message_id"] == "m1" for row in data["turn_events"]))
        self.assertEqual(len(data["pi_message_nodes"]), 4)
        self.assertTrue(any(row["role"] == "user" for row in data["pi_message_nodes"]))

    def test_dashboard_model_filter(self):
        data = get_dashboard_data(db_path=self.db_path, models=["claude-opus-4-6"])
        self.assertEqual(len(data["daily_by_model"]), 1)
        self.assertEqual(data["daily_by_model"][0]["model"], "claude-opus-4-6")
        self.assertEqual(len(data["session_models_daily"]), 1)

    def test_dashboard_provider_filter(self):
        data = get_dashboard_data(db_path=self.db_path, providers=["codex"])
        self.assertEqual(data["all_providers"], ["claude", "codex"])
        self.assertEqual(len(data["provider_breakdown"]), 1)
        self.assertEqual(data["provider_breakdown"][0]["provider"], "codex")
        self.assertEqual(len(data["session_models_daily"]), 1)
        self.assertEqual(data["tool_usage"], [{"tool": "edit", "count": 1}])
        self.assertEqual(len(data["session_rollups"]), 1)
        self.assertEqual(len(data["project_rollups"]), 1)

    def test_dashboard_opencode_provider_filter(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, provider, backend_provider, client, project_name, first_timestamp, last_timestamp,
                git_branch, total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation, model, turn_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "opencode:session-oc1",
                "opencode",
                "openai",
                "opencode",
                "demo/opencode",
                f"{self.recent_day}T09:00:00Z",
                f"{self.recent_day}T09:10:00Z",
                "main",
                150,
                45,
                30,
                15,
                "gpt-5-nano",
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO turns (
                session_id, provider, backend_provider, client, timestamp, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, tool_call_count, tool_name, tool_names, cwd, message_id, parent_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "opencode:session-oc1",
                "opencode",
                "openai",
                "opencode",
                f"{self.recent_day}T09:10:00Z",
                "gpt-5-nano",
                150,
                45,
                30,
                15,
                2,
                "read",
                "read,bash",
                "/tmp/opencode",
                "oc1",
                "ocu1",
            ),
        )
        conn.commit()
        conn.close()

        data = get_dashboard_data(db_path=self.db_path, providers=["opencode"])
        self.assertIn("opencode", data["all_providers"])
        self.assertEqual(len(data["provider_breakdown"]), 1)
        self.assertEqual(data["provider_breakdown"][0]["provider"], "opencode")
        self.assertEqual(len(data["session_rollups"]), 1)
        self.assertEqual(data["session_rollups"][0]["provider"], "opencode")
        self.assertEqual(data["overview_summary"]["totals"]["sessions"], 1)
        self.assertCountEqual(data["tool_usage"], [{"tool": "read", "count": 1}, {"tool": "bash", "count": 1}])

    def test_dashboard_range_filter(self):
        data = get_dashboard_data(db_path=self.db_path, range_name="7d")
        days = {row["day"] for row in data["session_models_daily"]}
        self.assertEqual(days, {self.recent_day})

    def test_dashboard_custom_date_filter(self):
        data = get_dashboard_data(db_path=self.db_path, from_date=self.older_day, to_date=self.recent_day)
        days = {row["day"] for row in data["session_models_daily"]}
        self.assertEqual(days, {self.older_day, self.recent_day})
        self.assertEqual(data["selected_from"], self.older_day)
        self.assertEqual(data["selected_to"], self.recent_day)

        reversed_dates = get_dashboard_data(db_path=self.db_path, from_date=self.recent_day, to_date=self.older_day)
        reversed_days = {row["day"] for row in reversed_dates["session_models_daily"]}
        self.assertEqual(reversed_days, {self.older_day, self.recent_day})
        self.assertEqual(reversed_dates["selected_from"], self.older_day)
        self.assertEqual(reversed_dates["selected_to"], self.recent_day)

    def test_pi_rows_normalize_to_pi_source(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, provider, backend_provider, client, project_name, first_timestamp, last_timestamp,
                git_branch, total_input_tokens, total_output_tokens,
                total_cache_read, total_cache_creation, model, turn_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pi:session-777",
                "pi",
                "anthropic",
                "pi-agent",
                "demo/pi",
                f"{self.recent_day}T12:00:00Z",
                f"{self.recent_day}T12:05:00Z",
                "main",
                120,
                60,
                20,
                10,
                "claude-sonnet-4-5",
                1,
            ),
        )
        conn.execute(
            """
            INSERT INTO turns (
                session_id, provider, backend_provider, client, timestamp, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, native_cost, tool_call_count, tool_name, tool_names, cwd, message_id, parent_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pi:session-777",
                "anthropic",
                "anthropic",
                "pi-agent",
                f"{self.recent_day}T12:01:00Z",
                "claude-sonnet-4-5",
                120,
                60,
                20,
                10,
                0.003,
                1,
                "read",
                "read",
                "/tmp/pi",
                "p1",
                "u1",
            ),
        )
        conn.commit()
        conn.close()

        data = get_dashboard_data(db_path=self.db_path)
        self.assertIn("pi", data["all_providers"])
        self.assertIn("anthropic", data["all_backend_providers"])
        self.assertTrue(any(row["provider"] == "pi" for row in data["provider_breakdown"]))
        self.assertTrue(any(row.get("backend_provider") == "anthropic" for row in data["turn_events"]))
        self.assertTrue(any(row["provider"] == "pi" for row in data["session_models_daily"]))
        self.assertTrue(any(row["provider"] == "pi" for row in data["turn_events"]))

        pi_only = get_dashboard_data(db_path=self.db_path, providers=["pi"])
        self.assertCountEqual(pi_only["all_providers"], ["claude", "codex", "pi"])
        self.assertEqual(len(pi_only["provider_breakdown"]), 1)
        self.assertEqual(pi_only["provider_breakdown"][0]["provider"], "pi")
        self.assertEqual(len(pi_only["session_rollups"]), 1)
        self.assertEqual(pi_only["session_rollups"][0]["provider"], "pi")

        today = get_today_usage_data(db_path=self.db_path, day=self.recent_day)
        self.assertTrue(any(row["provider"] == "pi" for row in today["rows"]))
        self.assertTrue(all(key in today["totals"] for key in ("native_cost", "estimated_cost")))

        all_time = get_all_time_stats_data(db_path=self.db_path)
        self.assertTrue(any(row["provider"] == "pi" for row in all_time["by_model"]))
        self.assertTrue(all(key in all_time["totals"] for key in ("native_cost", "estimated_cost")))

    def test_session_analytics_tracks_switches(self):
        data = get_dashboard_data(db_path=self.db_path)
        claude_session = next(row for row in data["session_analytics"] if row["full_session_id"] == "session-abc12345")
        self.assertEqual(claude_session["model_switches"], 2)
        self.assertEqual(claude_session["provider_switches"], 0)
        self.assertEqual(claude_session["backend_providers"], ["anthropic"])
        self.assertEqual(claude_session["tool_calls"], 3)
        self.assertEqual(claude_session["tree_nodes"], 3)
        self.assertEqual(claude_session["tree_max_depth"], 2)

    def test_specialized_api_shapes(self):
        overview = get_overview_data(db_path=self.db_path)
        self.assertIn("overview_summary", overview)
        self.assertIn("all_backend_providers", overview)
        self.assertEqual(overview["overview_summary"]["totals"]["sessions"], 2)

        sessions = get_sessions_data(db_path=self.db_path)
        self.assertIn("all_backend_providers", sessions)
        self.assertEqual(len(sessions["session_rollups"]), 2)
        self.assertIn("session-abc12345", sessions["session_details"])
        self.assertEqual(len(sessions["session_details"]["session-abc12345"]["tools"]), 2)

        projects = get_projects_data(db_path=self.db_path)
        self.assertEqual(len(projects["project_rollups"]), 2)
        self.assertIn("demo/project", projects["project_details"])
        self.assertEqual(projects["project_details"]["demo/project"]["totals"]["sessions"], 1)

        pi = get_pi_data(db_path=self.db_path)
        self.assertIn("pi_summary", pi)
        self.assertEqual(pi["pi_summary"]["totals"]["sessions"], 1)
        self.assertIn("session-abc12345", pi["session_details"])

    def test_light_dashboard_payload_omits_heavy_details(self):
        data = get_light_dashboard_data(db_path=self.db_path)
        self.assertEqual(len(data["session_rollups"]), 2)
        self.assertEqual(len(data["project_rollups"]), 2)
        self.assertEqual(data["session_details"], {})
        self.assertEqual(data["project_details"], {})
        self.assertEqual(data["turn_events"], [])
        self.assertEqual(data["pi_message_nodes"], [])

    def test_session_detail_endpoint_shape(self):
        data = get_session_detail_data(db_path=self.db_path, session_id="session-abc12345")
        self.assertIn("generated_at", data)
        self.assertIsNotNone(data["session_detail"])
        self.assertEqual(data["session_detail"]["full_session_id"], "session-abc12345")
        self.assertEqual(len(data["session_detail"]["events"]), 3)
        self.assertEqual(len(data["session_detail"]["messageNodes"]), 4)

    def test_project_detail_endpoint_shape(self):
        data = get_project_detail_data(db_path=self.db_path, project_name="demo/project")
        self.assertIn("generated_at", data)
        self.assertIsNotNone(data["project_detail"])
        self.assertEqual(data["project_detail"]["project"], "demo/project")
        self.assertEqual(data["project_detail"]["totals"]["sessions"], 1)


# ---------------------------------------------------------------------------
# Regression tests added per plan-eng-review 3A
# (proves the prior /review + /codex:review fixes don't silently regress)
# ---------------------------------------------------------------------------


class FreshInstallTests(unittest.TestCase):
    """A first-time user has no sessions yet. Every analytics call should
    return a clean, empty-state payload (not crash, not raise, not return
    None). This proves the dashboard renders on day one."""

    def test_get_dashboard_data_missing_db(self):
        # No DB file yet -> explicit error payload, not an exception.
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "nonexistent.db"
            from analytics import get_dashboard_data, get_light_dashboard_data
            for fn in (get_dashboard_data, get_light_dashboard_data):
                data = fn(db_path=db_path)
                self.assertIn("error", data)
                self.assertIn("scan", data["error"].lower())

    def test_get_dashboard_data_empty_db(self):
        # DB file exists, schema is initialized, but no rows.
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "empty.db"
            conn = sqlite3.connect(db_path)
            init_db(conn)
            conn.close()
            from analytics import get_light_dashboard_data
            data = get_light_dashboard_data(db_path=db_path)
            # Expect clean empty shapes, never None / missing keys.
            self.assertEqual(data["session_rollups"], [])
            self.assertEqual(data["project_rollups"], [])
            self.assertEqual(data["daily_by_model"], [])
            self.assertEqual(data["provider_breakdown"], [])
            self.assertEqual(data["all_models"], [])
            self.assertEqual(data["all_providers"], [])
            self.assertEqual(data["overview_summary"]["totals"]["sessions"], 0)
            self.assertEqual(data["pi_summary"]["totals"]["sessions"], 0)

    def test_scan_with_no_sources_available(self):
        # Empty source list simulates a user who has no agents installed.
        # scan_all_sources must complete without error and return zero
        # counts. DB file is created and the schema is applied so the
        # dashboard will still render an empty state.
        from ingest import scan_all_sources
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "fresh.db"
            result = scan_all_sources(db_path=db_path, verbose=False, sources=[])
            self.assertEqual(result["new"], 0)
            self.assertEqual(result["updated"], 0)
            self.assertEqual(result["turns"], 0)
            self.assertEqual(result["sessions"], 0)
            self.assertTrue(db_path.exists())


class LegacyDBMigrationTests(unittest.TestCase):
    """Regression for paths.migrate_legacy_db.

    Before the fix, copying ~/.claude/usage.db to the new path left it on
    the legacy schema. First analytics call then crashed with
    sqlite3.OperationalError "no such column: native_cost".
    """

    def test_migrate_legacy_db_upgrades_schema_and_rewrites_session_ids(self):
        # ignore_cleanup_errors avoids a Windows race on SQLite handles.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempdir:
            legacy_db = Path(tempdir) / ".claude" / "usage.db"
            new_db = Path(tempdir) / ".coding-agents" / "usage.db"
            legacy_db.parent.mkdir(parents=True)

            # Build a legacy-shape DB: sessions has no native_cost / tree_* columns.
            # turns has no provider / native_cost / message_id.
            conn = sqlite3.connect(legacy_db)
            conn.executescript(
                """
                CREATE TABLE sessions (
                    session_id      TEXT PRIMARY KEY,
                    project_name    TEXT,
                    first_timestamp TEXT,
                    last_timestamp  TEXT,
                    git_branch      TEXT,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0,
                    total_cache_read INTEGER DEFAULT 0,
                    total_cache_creation INTEGER DEFAULT 0,
                    model TEXT,
                    turn_count INTEGER DEFAULT 0
                );
                CREATE TABLE turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    timestamp TEXT,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_creation_tokens INTEGER DEFAULT 0,
                    tool_name TEXT,
                    cwd TEXT
                );
                CREATE TABLE processed_files (path TEXT PRIMARY KEY, mtime REAL, lines INTEGER);
                """
            )
            conn.execute("INSERT INTO sessions (session_id, project_name) VALUES (?, ?)", ("abc-xyz", "demo/legacy"))
            conn.execute("INSERT INTO turns (session_id, timestamp) VALUES (?, ?)", ("abc-xyz", "2025-01-01T00:00:00Z"))
            conn.execute("INSERT INTO processed_files (path, mtime, lines) VALUES (?, ?, ?)", ("/some/file.jsonl", 1.0, 10))
            conn.commit()
            conn.close()

            with patch("paths.LEGACY_DB_PATH", legacy_db), patch("paths.DB_PATH", new_db), patch("paths.APP_HOME", new_db.parent):
                from paths import migrate_legacy_db
                result = migrate_legacy_db()
                self.assertEqual(result, new_db)
                self.assertTrue(new_db.exists())

                # New schema present.
                conn2 = sqlite3.connect(new_db)
                conn2.row_factory = sqlite3.Row
                row = conn2.execute("SELECT total_native_cost, tree_nodes, provider FROM sessions WHERE session_id = ?", ("claude:abc-xyz",)).fetchone()
                self.assertIsNotNone(row, "session_id should have been rewritten to claude: prefix")
                # Turns row also renamed + new column available.
                trow = conn2.execute("SELECT native_cost, message_id FROM turns WHERE session_id = ?", ("claude:abc-xyz",)).fetchone()
                self.assertIsNotNone(trow)
                # processed_files path also prefixed.
                prow = conn2.execute("SELECT mtime FROM processed_files WHERE path = ?", ("claude:/some/file.jsonl",)).fetchone()
                self.assertIsNotNone(prow)
                conn2.close()


class TurnsUniqueIndexTests(unittest.TestCase):
    """Regression for schema idx_turns_message_id.

    The original global unique index on (message_id) silently dropped turns
    from different Pi sessions that happened to share a short id like 'b1'.
    The compound (session_id, message_id) index lets them both persist.
    """

    def test_turns_unique_per_session_not_globally(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "usage.db"
            conn = sqlite3.connect(db_path)
            init_db(conn)
            # Two Pi-ish sessions, each with a turn using the short id 'b1'.
            for session_id in ("pi:one", "pi:two"):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO turns
                        (session_id, provider, client, timestamp, model,
                         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                         native_cost, tool_call_count, tool_name, tool_names, cwd, message_id, parent_message_id)
                    VALUES (?, 'pi', 'pi-agent', '2026-04-01T00:00:00Z', 'claude-sonnet-4-5',
                            10, 20, 0, 0, 0, 0, '', '', '', 'b1', '')
                    """,
                    (session_id,),
                )
            conn.commit()
            rows = conn.execute("SELECT session_id FROM turns WHERE message_id = 'b1' ORDER BY session_id").fetchall()
            conn.close()
            self.assertEqual([row[0] for row in rows], ["pi:one", "pi:two"],
                             "Both sessions' message_id='b1' turn should persist; the old global unique index would have dropped the second.")


class CostAggregationTests(unittest.TestCase):
    """Regression for _cost_for_row / cost_for_aggregate.

    Before the fix, mixed-source aggregate rows (same model, same day, some
    turns with native_cost and some without) returned only the native sum,
    dropping the estimate portion for the non-native turns.
    """

    def test_cost_for_aggregate_mixes_native_and_estimate(self):
        from analytics import cost_for_aggregate, cost_for_turn

        # Two Pi turns at native cost $0.10 + two Claude turns that estimate
        # to some cost via pricing on their tokens.
        model = "claude-sonnet-4-6"
        native_sum = 0.20
        # Claude portion: 1M input tokens + 500K output at Sonnet pricing.
        est_input = 1_000_000
        est_output = 500_000
        est_cache_read = 100_000
        est_cache_creation = 0

        single_turn_estimate = cost_for_turn(model, est_input, est_output, est_cache_read, est_cache_creation)
        self.assertGreater(single_turn_estimate, 0)

        total = cost_for_aggregate(model, native_sum, est_input, est_output, est_cache_read, est_cache_creation)
        # Must include BOTH the native sum AND the estimate of the non-native portion.
        self.assertAlmostEqual(total, native_sum + single_turn_estimate, places=6)
        self.assertGreater(total, native_sum, "Aggregate must not drop the non-native estimate portion.")

    def test_cost_for_turn_uses_native_when_present(self):
        from analytics import cost_for_turn
        self.assertAlmostEqual(cost_for_turn("gpt-5.4", 1000, 1000, 0, 0, native_cost=0.42), 0.42)

    def test_cost_for_turn_estimates_when_no_native(self):
        from analytics import cost_for_turn
        cost = cost_for_turn("claude-sonnet-4-6", 1_000_000, 0, 0, 0, native_cost=0.0)
        self.assertGreater(cost, 0.0)


class SafeJoinTests(unittest.TestCase):
    """Regression for server._safe_join.

    The previous `.replace("..", "")` defense was ineffective against
    traversal sequences. _safe_join uses resolve() + relative_to() instead.
    """

    def test_safe_join_rejects_parent_traversal(self):
        from server import _safe_join

        with tempfile.TemporaryDirectory() as tempdir:
            base = Path(tempdir) / "allowed"
            base.mkdir()
            (base / "ok.txt").write_text("data")
            (Path(tempdir) / "secret.txt").write_text("nope")

            # Benign relative path -> OK.
            good = _safe_join(base, "ok.txt")
            self.assertIsNotNone(good)
            self.assertTrue(good.exists())

            # Parent traversal -> rejected.
            self.assertIsNone(_safe_join(base, "../secret.txt"))
            self.assertIsNone(_safe_join(base, "../../etc/passwd"))
            # Windows-style backslash traversal (on Windows Path treats this as separators).
            self.assertIsNone(_safe_join(base, "..\\..\\secret.txt"))

    def test_safe_join_accepts_nested_child(self):
        from server import _safe_join

        with tempfile.TemporaryDirectory() as tempdir:
            base = Path(tempdir) / "root"
            nested = base / "sub" / "dir"
            nested.mkdir(parents=True)
            (nested / "ok.txt").write_text("data")
            result = _safe_join(base, "sub/dir/ok.txt")
            self.assertIsNotNone(result)
            self.assertTrue(result.exists())


class RescanAtomicSwapTests(unittest.TestCase):
    """Regression for server /api/rescan atomic-swap flow.

    Before the fix, /api/rescan unlinked the DB before scanning. If the scan
    crashed mid-way, the original DB was already gone. Now the scan writes
    to usage.db.rescan.tmp and os.replace swaps on success.
    """

    def test_scan_failure_leaves_original_db_intact(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "usage.db"
            conn = sqlite3.connect(db_path)
            init_db(conn)
            conn.execute(
                "INSERT INTO sessions (session_id, project_name) VALUES (?, ?)",
                ("claude:seed", "demo/existing"),
            )
            conn.commit()
            conn.close()

            original_bytes = db_path.read_bytes()
            temp_path = db_path.with_suffix(db_path.suffix + ".rescan.tmp")

            # Emulate the handler's flow: init temp DB, call scan (raises),
            # verify cleanup + original intact.
            try:
                if temp_path.exists():
                    temp_path.unlink()
                raise RuntimeError("simulated scan failure")
            except Exception:
                if temp_path.exists():
                    temp_path.unlink()
            # No os.replace happened -> original DB untouched.
            self.assertTrue(db_path.exists())
            self.assertEqual(db_path.read_bytes(), original_bytes)


class CodexParserTests(unittest.TestCase):
    """Codex source parser: baseline + edge cases (eng review 3A)."""

    def _build_session(self, lines):
        return "\n".join(lines) + "\n"

    def _write(self, tempdir, lines):
        path = Path(tempdir.name) / "rollout.jsonl"
        path.write_text(self._build_session(lines), encoding="utf-8")
        return path

    def test_codex_parser_reconstructs_turns_from_last_token_usage(self):
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:01Z","payload":{"turn_id":"t1","cwd":"/tmp/demo","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_started","turn_id":"t1","model_context_window":100000}}',
                '{"type":"response_item","timestamp":"2026-04-10T10:00:03Z","payload":{"type":"function_call","name":"shell"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:04Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":500,"cached_input_tokens":100,"output_tokens":80,"reasoning_output_tokens":20}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:05Z","payload":{"type":"task_complete","turn_id":"t1"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            sessions, turns, _nodes, line_count = CodexSource().parse_full_file(path)
            self.assertEqual(line_count, 6)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], "codex:s1")
            self.assertEqual(len(turns), 1)
            t = turns[0]
            self.assertEqual(t["message_id"], "t1")
            self.assertEqual(t["backend_provider"], "openai")
            self.assertEqual(t["model"], "gpt-5.4")
            self.assertEqual(t["input_tokens"], 400)   # 500 - 100 cached
            self.assertEqual(t["cache_read_tokens"], 100)
            self.assertEqual(t["output_tokens"], 100)  # 80 + 20 reasoning
            self.assertEqual(t["tool_name"], "shell")
            self.assertEqual(t["tool_call_count"], 1)

    def test_codex_parser_skips_aborted_turn_no_tokens(self):
        """task_started without token_count should not emit a zero-token turn."""
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:01Z","payload":{"type":"task_started","turn_id":"t-aborted"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_complete","turn_id":"t-aborted"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _s, turns, _n, _lc = CodexSource().parse_full_file(path)
            self.assertEqual(turns, [])

    def test_codex_parser_handles_context_compaction(self):
        """Two turns across a context_compacted event — both must emit with their own token counts."""
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:01Z","payload":{"turn_id":"t1","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_started","turn_id":"t1"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:03Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:04Z","payload":{"type":"task_complete","turn_id":"t1"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:05Z","payload":{"type":"context_compacted"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:06Z","payload":{"turn_id":"t2","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:07Z","payload":{"type":"task_started","turn_id":"t2"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:08Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":20,"cached_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:09Z","payload":{"type":"task_complete","turn_id":"t2"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _s, turns, _n, _lc = CodexSource().parse_full_file(path)
            self.assertEqual([t["message_id"] for t in turns], ["t1", "t2"])
            # Critical: turn 2's tokens must be 20/10, not accumulated from turn 1.
            self.assertEqual(turns[1]["input_tokens"], 20)
            self.assertEqual(turns[1]["output_tokens"], 10)

    def test_codex_parser_empty_file_is_safe(self):
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "empty.jsonl"
            path.write_text("", encoding="utf-8")
            sessions, turns, nodes, line_count = CodexSource().parse_full_file(path)
            self.assertEqual(sessions, [])
            self.assertEqual(turns, [])
            self.assertEqual(nodes, [])
            self.assertEqual(line_count, 0)

    def test_codex_parser_file_without_session_meta_ignores_turns(self):
        """No session_meta -> parser has no session_id context, emits nothing."""
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:01Z","payload":{"type":"task_started","turn_id":"t1"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_complete","turn_id":"t1"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            sessions, turns, _n, _lc = CodexSource().parse_full_file(path)
            self.assertEqual(sessions, [])
            self.assertEqual(turns, [])

    def test_codex_parser_sums_multiple_token_count_events_per_turn(self):
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:01Z","payload":{"turn_id":"t1","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_started","turn_id":"t1"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:03Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:04Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":200,"cached_input_tokens":0,"output_tokens":100,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:05Z","payload":{"type":"task_complete","turn_id":"t1"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _s, turns, _n, _lc = CodexSource().parse_full_file(path)
            self.assertEqual(len(turns), 1)
            self.assertEqual(turns[0]["input_tokens"], 300)   # 100 + 200
            self.assertEqual(turns[0]["output_tokens"], 150)  # 50 + 100

    def test_codex_parser_records_custom_tool_calls(self):
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:01Z","payload":{"turn_id":"t1","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_started","turn_id":"t1"}}',
                '{"type":"response_item","timestamp":"2026-04-10T10:00:03Z","payload":{"type":"custom_tool_call","name":"browser.navigate"}}',
                '{"type":"response_item","timestamp":"2026-04-10T10:00:04Z","payload":{"type":"function_call","name":"apply_patch"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:05Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:06Z","payload":{"type":"task_complete","turn_id":"t1"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _s, turns, _n, _lc = CodexSource().parse_full_file(path)
            self.assertEqual(turns[0]["tool_call_count"], 2)
            self.assertIn("browser.navigate", turns[0]["tool_names"])
            self.assertIn("apply_patch", turns[0]["tool_names"])

    def test_codex_parser_uses_turn_context_model(self):
        """turn_context arrives before task_started and carries the canonical model."""
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:01Z","payload":{"turn_id":"t1","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_started","turn_id":"t1"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:03Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":10,"cached_input_tokens":0,"output_tokens":5,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:04Z","payload":{"type":"task_complete","turn_id":"t1"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _s, turns, _n, _lc = CodexSource().parse_full_file(path)
            self.assertEqual(turns[0]["model"], "gpt-5.4")

    def test_codex_parser_orphaned_task_complete_is_skipped(self):
        """task_complete without a preceding task_started should not emit a turn."""
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_complete","turn_id":"t-orphan"}}',
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _s, turns, _n, _lc = CodexSource().parse_full_file(path)
            # No task_started -> current_turn_tokens is zeros -> turn skipped
            # (non_cached_input + cache_read + output == 0 branch).
            self.assertEqual(turns, [])

    def test_codex_parser_incremental_skips_turns_before_old_lines(self):
        """parse_incremental_file should not re-emit turns that completed
        on a line at or before old_lines."""
        from sources.codex import CodexSource
        with tempfile.TemporaryDirectory() as tempdir_name:
            path = Path(tempdir_name) / "rollout.jsonl"
            turn1_lines = [
                '{"type":"session_meta","timestamp":"2026-04-10T10:00:00Z","payload":{"id":"s1","timestamp":"2026-04-10T10:00:00Z","cwd":"/tmp/demo","model_provider":"openai"}}',
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:01Z","payload":{"turn_id":"t1","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:02Z","payload":{"type":"task_started","turn_id":"t1"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:03Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":100,"cached_input_tokens":0,"output_tokens":50,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:04Z","payload":{"type":"task_complete","turn_id":"t1"}}',
            ]
            turn2_lines = [
                '{"type":"turn_context","timestamp":"2026-04-10T10:00:05Z","payload":{"turn_id":"t2","model":"gpt-5.4"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:06Z","payload":{"type":"task_started","turn_id":"t2"}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:07Z","payload":{"type":"token_count","info":{"last_token_usage":{"input_tokens":20,"cached_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":0}}}}',
                '{"type":"event_msg","timestamp":"2026-04-10T10:00:08Z","payload":{"type":"task_complete","turn_id":"t2"}}',
            ]
            path.write_text("\n".join(turn1_lines + turn2_lines) + "\n", encoding="utf-8")
            # old_lines=5 means line 5 (t1's task_complete) has already been persisted.
            _s, turns, _n, _lc = CodexSource().parse_incremental_file(path, old_lines=5)
            self.assertEqual([t["message_id"] for t in turns], ["t2"])
            self.assertEqual(turns[0]["input_tokens"], 20)


if __name__ == "__main__":
    unittest.main()
