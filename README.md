# BurnMap

> This is fine. Until you check your token burn.

![This is fine](docs/thisisfine.jfif)

BurnMap is a local-first analytics tool for coding-agent usage. It ingests usage data from Claude Code, Codex, Pi, and OpenCode, normalizes it into a local SQLite database, and gives you both a CLI and a browser dashboard to inspect token burn, session activity, model mix, tool usage, and cost.

If you received BurnMap as a **tester zip bundle**, start with **`START_HERE_TESTERS.md`** in the project root. That guide is written for zip-based testing and does not require Git or a frontend build step.

<!-- Dashboard screenshot (generated from synthetic demo data) coming shortly. -->
_The dashboard gives a populated overview: sessions, token burn, model mix, and estimated cost across all sources._

## Why BurnMap?

If you use more than one coding agent, usage gets expensive fast and visibility gets fragmented.

BurnMap helps you answer questions like:

- Which models are burning the most tokens?
- Which projects are consuming the most usage?
- Which sessions were the most expensive?
- How much came from Claude Code vs Codex vs Pi vs OpenCode?
- Where are tool calls clustering?
- How much cost is native vs estimated?

## What BurnMap tracks

- Sessions and turns across supported coding agents
- Input, output, cache-read, and cache-write token usage
- Source app and upstream model provider mix
- Tool-call frequency and tool names
- Project- and model-level rollups
- Estimated cost when native cost is unavailable
- Pi conversation-tree metrics
- OpenCode usage imported from its local SQLite database

## Features

- Scan local usage data from all supported sources:
  - Claude Code JSONL logs
  - Codex JSONL logs
  - Pi JSONL logs
  - OpenCode's local SQLite database (`opencode.db`)
- Store normalized usage data in a local SQLite database
- View daily and all-time usage from the command line
- Open a browser dashboard for sessions, projects, models, and Pi trees
- Filter by preset ranges or explicit `from` / `to` date bounds
- Background live scanning while the dashboard server is running
- Automatic dashboard refresh so newly-seen sessions appear without manual reloads
- Local-only operation; no third-party Python services required

## Requirements

- Python 3
- Local usage data from one or more supported tools
- No third-party Python packages required

Node.js is **not** required to run BurnMap. It is only needed if you want to rebuild the frontend assets in `frontend/dist/`.

## Installation

Clone the repository and enter the project directory:

```bash
git clone REPO_URL
cd BurnMap
```

Optional virtual environment.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Windows

```bash
py -3 -m venv .venv
.venv\Scripts\activate
```

## Quick start

### 1. Scan local usage data

```bash
python3 main.py scan
```

### 2. Check today's usage

```bash
python3 main.py today
```

### 3. Open the dashboard

```bash
python3 main.py dashboard
```

Then visit:

```text
http://localhost:8080/
```

If your system uses `py` instead of `python3`, substitute `py -3` in the examples above.

## CLI commands

```text
python main.py scan [--projects-dir PATH]   Scan usage files and update the database
python main.py rescan [--projects-dir PATH] Rebuild the database from scratch
python main.py today                        Show today's usage summary
python main.py stats                        Show all-time usage statistics
python main.py dashboard                    Scan + start the dashboard
python main.py serve                        Start the dashboard without an initial scan
python main.py test                         Run tests
```

## Supported data sources

BurnMap scans the following sources by default.

| Source | Default location | Format | Override |
|---|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` and `~/Library/Developer/Xcode/CodingAssistant/ClaudeAgentConfig/projects/**/*.jsonl` | JSONL | `--projects-dir PATH` |
| Codex | `~/.codex/sessions/**/*.jsonl` | JSONL | `CODEX_USAGE_DIR=/path/to/codex/sessions` |
| Pi | `~/.pi/agent/sessions/**/*.jsonl` | JSONL | `PI_USAGE_DIR=/path/to/pi/sessions` |
| OpenCode | `~/.local/share/opencode/opencode.db` | SQLite | `OPENCODE_DB_PATH=/path/to/opencode.db` |

### Example overrides

#### Codex

```bash
CODEX_USAGE_DIR=/path/to/codex/sessions python3 main.py scan
```

#### Pi

```bash
PI_USAGE_DIR=/path/to/pi/sessions python3 main.py scan
```

#### OpenCode

```bash
OPENCODE_DB_PATH=/path/to/opencode.db python3 main.py scan
```

### OpenCode note

OpenCode is different from the other sources: BurnMap reads its local SQLite database instead of JSONL session files.

When running under WSL, BurnMap can also detect Windows OpenCode databases under paths like:

```text
/mnt/c/Users/<user>/.local/share/opencode/opencode.db
```

## How the dashboard behaves

### `dashboard`

```bash
python3 main.py dashboard
```

This command:

1. runs an incremental scan,
2. prepares dashboard data,
3. starts the local server,
4. opens the browser.

### `serve`

```bash
python3 main.py serve
```

This starts the dashboard without an initial foreground scan. It is useful when you already scanned recently and just want to reopen the UI.

### Live updates

While the dashboard server is running, BurnMap performs background incremental scans every 30 seconds by default. The frontend also refreshes automatically, so new Claude/Codex/Pi/OpenCode activity should show up without a manual rescan.

### Routes

- Canonical app route: `/`
- Legacy `/app` and `/app/...` URLs redirect to `/`

## Configuration

### Database location

BurnMap stores its SQLite database at:

```text
~/.coding-agents/usage.db
```

If an older database exists at `~/.claude/usage.db`, BurnMap can migrate it automatically.

### Dashboard host and port

Default dashboard URL:

```text
http://localhost:8080/
```

Override with:

```bash
HOST=127.0.0.1 PORT=8090 python3 main.py dashboard
```

### Background scan interval

Default: every 30 seconds while the dashboard server is running.

Override or disable with:

```bash
AUTO_SCAN_SECONDS=10 python3 main.py dashboard
AUTO_SCAN_SECONDS=0 python3 main.py dashboard
```

## Cost model

BurnMap prefers native cost recorded by the source when it exists.

When native cost is missing, BurnMap estimates cost from known pricing tables. Today that includes Claude-family and GPT-5 / Codex-family pricing, and mixed native + estimated totals are reported correctly in aggregate views.

## Typical workflow

```bash
python3 main.py scan
python3 main.py today
python3 main.py stats
python3 main.py dashboard
```

## Troubleshooting

### The dashboard says "Database not found"

Run a scan first:

```bash
python3 main.py scan
```

### No data appears

Make sure your source data exists in the default locations, or point BurnMap at the correct path using one of:

- `--projects-dir`
- `CODEX_USAGE_DIR`
- `PI_USAGE_DIR`
- `OPENCODE_DB_PATH`

### OpenCode does not appear in the UI

Check the following:

1. run a scan at least once:

   ```bash
   python3 main.py scan
   ```

2. restart the dashboard server if it was already running,
3. hard-refresh the browser,
4. confirm that `OPENCODE_DB_PATH` points at the correct `opencode.db` if you are not using the default location.

After that, newly-used OpenCode sessions should continue to appear automatically while the dashboard remains open.

### The dashboard frontend looks stale

This repository serves prebuilt frontend assets from `frontend/dist/`.

If the browser still appears stale after an update, hard-refresh the page. If you are rebuilding the frontend yourself, rebuild the assets before restarting BurnMap.

### Cost numbers do not match exactly

Some sources record native cost and some do not. BurnMap will estimate cost where native cost is unavailable, so exact values may differ from vendor dashboards for partially-estimated sessions.

## Project structure

```text
main.py        CLI entrypoint
server.py      local dashboard server
ingest.py      source discovery and ingestion
store.py       SQLite persistence helpers
analytics/     SQL queries, rollups, and filter helpers
sources/       source-specific parsers
frontend/dist/ prebuilt dashboard assets
docs/          screenshots and documentation assets
```

## License

MIT. See [LICENSE](LICENSE).
