# BurnMap Tester Guide

This zip contains a **ready-to-run BurnMap test bundle**.

You do **not** need Git, Node.js, npm, pnpm, or a frontend build step to use it.

## What this bundle includes

- BurnMap Python backend
- Prebuilt web dashboard assets
- Support for:
  - Claude Code
  - Codex
  - Pi
  - OpenCode

## What you need

- **Python 3** installed
- Local usage data from at least one supported tool

Recommended: Python **3.10+**

---

## Quick start

### Windows

Open **PowerShell** in the unzipped BurnMap folder and run:

```powershell
py -3 main.py dashboard
```

### macOS / Linux

Open a terminal in the unzipped BurnMap folder and run:

```bash
python3 main.py dashboard
```

Then open:

```text
http://localhost:8080/
```

BurnMap should also try to open the browser automatically.

---

## First-time recommended flow

### 1. Unzip the folder

Extract the zip somewhere convenient, for example:

- Windows: `Desktop\BurnMap`
- macOS / Linux: `~/BurnMap`

### 2. Open a terminal in that folder

You should see files like:

- `main.py`
- `server.py`
- `frontend/`
- `analytics/`
- `sources/`

### 3. Run the dashboard

Windows:

```powershell
py -3 main.py dashboard
```

macOS / Linux:

```bash
python3 main.py dashboard
```

This command will:

1. scan your local usage data,
2. build the initial dashboard cache,
3. start the local web server,
4. open the dashboard in your browser.

---

## No Node.js required

The frontend is already prebuilt and included in this bundle.

You do **not** need to run:

- `npm install`
- `npm run build`
- `pnpm install`
- `pnpm build`

---

## Useful commands

### Scan usage data

Windows:

```powershell
py -3 main.py scan
```

macOS / Linux:

```bash
python3 main.py scan
```

### Rebuild the local database from scratch

Windows:

```powershell
py -3 main.py rescan
```

macOS / Linux:

```bash
python3 main.py rescan
```

### Show today's usage

Windows:

```powershell
py -3 main.py today
```

macOS / Linux:

```bash
python3 main.py today
```

### Show all-time stats

Windows:

```powershell
py -3 main.py stats
```

macOS / Linux:

```bash
python3 main.py stats
```

### Start the dashboard without an initial scan

Windows:

```powershell
py -3 main.py serve
```

macOS / Linux:

```bash
python3 main.py serve
```

### Run the test suite

Windows:

```powershell
py -3 main.py test
```

macOS / Linux:

```bash
python3 main.py test
```

---

## Where BurnMap stores its data

BurnMap stores its database here:

```text
~/.coding-agents/usage.db
```

That means:

- your original source logs are not modified,
- BurnMap builds its own local SQLite database,
- rescanning is safe.

---

## Supported sources and default locations

### Claude Code

- `~/.claude/projects/**/*.jsonl`
- `~/Library/Developer/Xcode/CodingAssistant/ClaudeAgentConfig/projects/**/*.jsonl`

### Codex

- `~/.codex/sessions/**/*.jsonl`

### Pi

- `~/.pi/agent/sessions/**/*.jsonl`

### OpenCode

- `~/.local/share/opencode/opencode.db`

OpenCode is read from its local **SQLite database**, not JSONL logs.

---

## If your data lives somewhere else

You can override source locations.

### Codex

Windows PowerShell:

```powershell
$env:CODEX_USAGE_DIR = 'C:\path\to\codex\sessions'
py -3 main.py scan
```

macOS / Linux:

```bash
CODEX_USAGE_DIR=/path/to/codex/sessions python3 main.py scan
```

### Pi

Windows PowerShell:

```powershell
$env:PI_USAGE_DIR = 'C:\path\to\pi\sessions'
py -3 main.py scan
```

macOS / Linux:

```bash
PI_USAGE_DIR=/path/to/pi/sessions python3 main.py scan
```

### OpenCode

Windows PowerShell:

```powershell
$env:OPENCODE_DB_PATH = 'C:\path\to\opencode.db'
py -3 main.py scan
```

macOS / Linux:

```bash
OPENCODE_DB_PATH=/path/to/opencode.db python3 main.py scan
```

### Claude custom projects directory

Windows PowerShell:

```powershell
py -3 main.py scan --projects-dir C:\path\to\claude\projects
```

macOS / Linux:

```bash
python3 main.py scan --projects-dir /path/to/claude/projects
```

---

## Dashboard behavior

Default dashboard URL:

```text
http://localhost:8080/
```

While the dashboard server is running:

- BurnMap performs background incremental scans every 30 seconds by default
- the frontend refreshes automatically
- new sessions should appear without a manual full reload in normal use

---

## Change the port if 8080 is busy

### Windows PowerShell

```powershell
$env:PORT = '8090'
py -3 main.py dashboard
```

Then visit:

```text
http://localhost:8090/
```

### macOS / Linux

```bash
PORT=8090 python3 main.py dashboard
```

---

## Troubleshooting

### 1. The dashboard says no data / looks empty

Run:

Windows:

```powershell
py -3 main.py scan
```

macOS / Linux:

```bash
python3 main.py scan
```

Then restart the dashboard.

### 2. OpenCode does not appear

Try this sequence:

1. run a scan,
2. restart the dashboard server,
3. hard-refresh the browser,
4. set `OPENCODE_DB_PATH` manually if your OpenCode DB is not in the default location.

### 3. The browser did not open automatically

Open it manually:

```text
http://localhost:8080/
```

### 4. Python command not found

Try one of these depending on your system:

- `py -3`
- `python3`
- `python`

### 5. The dashboard looks stale

Do a hard refresh in the browser:

- Windows / Linux: `Ctrl + Shift + R`
- macOS: `Cmd + Shift + R`

---

## What to send back when reporting a bug

Please include:

1. your OS
2. the command you ran
3. the full error message or traceback
4. a screenshot if the issue is in the dashboard
5. which source was involved:
   - Claude Code
   - Codex
   - Pi
   - OpenCode
6. whether the bug affects:
   - scanning
   - missing sessions
   - filters
   - cost numbers
   - dashboard rendering
   - OpenCode visibility

Helpful extra info:

- output of `today`
- output of `stats`
- whether `scan` or `rescan` changes the result

---

## Privacy note

BurnMap is designed to run locally.

It reads your local usage data and builds a local SQLite database for analysis. It does not require a hosted backend or a cloud account.

---

## If you just want one command to try first

Windows:

```powershell
py -3 main.py dashboard
```

macOS / Linux:

```bash
python3 main.py dashboard
```
