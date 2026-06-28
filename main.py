"""
main.py - Command-line entrypoint for the multi-agent coding usage monitor.
"""

import importlib.util
import os
import sys
from pathlib import Path

from analytics import get_all_time_stats_data, get_today_usage_data, source_label


def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_cost(cost):
    return f"${cost:.4f}"


def fmt_cost_breakdown(native_cost, estimated_cost):
    return f"native={fmt_cost(native_cost)} est={fmt_cost(estimated_cost)}"


def hr(char="-", width=60):
    print(char * width)


def cmd_scan(projects_dir=None):
    from ingest import scan_all_sources

    return scan_all_sources(projects_dir=Path(projects_dir) if projects_dir else None)


def cmd_rescan(projects_dir=None):
    from ingest import rebuild_database

    return rebuild_database(projects_dir=Path(projects_dir) if projects_dir else None)


def cmd_today():
    data = get_today_usage_data()
    if data.get("error"):
        print(data["error"])
        sys.exit(1)

    print()
    hr()
    print(f"  Today's Usage  ({data['day']})")
    hr()

    if not data["rows"]:
        print("  No usage recorded today.")
        print()
        return

    for row in data["rows"]:
        provider = source_label(row.get("provider"))
        breakdown = fmt_cost_breakdown(row.get("native_cost", 0.0), row.get("estimated_cost", 0.0))
        print(f"  {provider:<12} {row['model']:<26} turns={row['turns']:<4} in={fmt(row['input']):<8} out={fmt(row['output']):<8} cost={fmt_cost(row['cost'])}  {breakdown}")

    totals = data["totals"]
    hr()
    print(f"  {'TOTAL':<37} turns={totals['turns']:<4} in={fmt(totals['input']):<8} out={fmt(totals['output']):<8} cost={fmt_cost(totals['cost'])}  {fmt_cost_breakdown(totals.get('native_cost', 0.0), totals.get('estimated_cost', 0.0))}")
    print()
    print(f"  Sessions today:   {totals['sessions']}")
    print(f"  Cache read:       {fmt(totals['cache_read'])}")
    print(f"  Cache creation:   {fmt(totals['cache_creation'])}")
    hr()
    print()


def cmd_stats():
    data = get_all_time_stats_data()
    if data.get("error"):
        print(data["error"])
        sys.exit(1)

    print()
    hr("=")
    print("  Coding Agent Usage - All-Time Statistics")
    hr("=")
    print(f"  Period:           {data['period']['first']} to {data['period']['last']}")
    print(f"  Total sessions:   {data['totals']['sessions'] or 0:,}")
    print(f"  Total turns:      {fmt(data['totals']['turns'] or 0)}")
    print()
    print(f"  Input tokens:     {fmt(data['totals']['input'] or 0):<12}")
    print(f"  Output tokens:    {fmt(data['totals']['output'] or 0):<12}")
    print(f"  Cache read:       {fmt(data['totals']['cache_read'] or 0):<12}")
    print(f"  Cache creation:   {fmt(data['totals']['cache_creation'] or 0):<12}")
    print(f"  Est. total cost:  {fmt_cost(data['totals']['cost'])}")
    print(f"  Native cost:      {fmt_cost(data['totals'].get('native_cost', 0.0))}")
    print(f"  Estimated cost:   {fmt_cost(data['totals'].get('estimated_cost', 0.0))}")
    print()
    print("  By Source / Model:")
    for row in data["by_model"]:
        provider = source_label(row.get("provider"))
        breakdown = fmt_cost_breakdown(row.get("native_cost", 0.0), row.get("estimated_cost", 0.0))
        print(f"    {provider:<12} {row['model']:<26} sessions={row['sessions']:<4} turns={fmt(row['turns'] or 0):<6} in={fmt(row['input'] or 0):<8} out={fmt(row['output'] or 0):<8} cost={fmt_cost(row['cost'])}  {breakdown}")
    hr("=")
    print()


def _start_server(open_browser=True):
    import threading
    import time
    import webbrowser

    from server import serve

    host = os.environ.get("HOST", "localhost")
    port = int(os.environ.get("PORT", "8080"))
    url = f"http://{host}:{port}/"

    print(f"\nStarting dashboard server at {url}")
    if open_browser:
        print("Opening browser...")

        def launch_browser():
            time.sleep(1.0)
            webbrowser.open(url)

        thread = threading.Thread(target=launch_browser, daemon=True)
        thread.start()

    serve(host=host, port=port)


def cmd_dashboard(projects_dir=None):
    print("Scanning sources...")
    result = cmd_scan(projects_dir=projects_dir)
    print(
        "Scan complete: "
        f"{result['new']} new, "
        f"{result['updated']} updated, "
        f"{result['skipped']} skipped, "
        f"{result['turns']} turns added"
    )
    print("Preparing initial dashboard data cache...")
    from analytics import get_light_dashboard_data

    get_light_dashboard_data()
    _start_server(open_browser=True)


def cmd_serve():
    print("Starting dashboard without pre-scan...")
    try:
        print("Preparing initial dashboard data cache...")
        from analytics import get_light_dashboard_data

        get_light_dashboard_data()
    except Exception as exc:
        print(f"Initial cache prewarm skipped: {exc}")
    _start_server(open_browser=True)


def cmd_test():
    tests_path = Path(__file__).with_name("tests.py")
    if not tests_path.exists():
        print(f"Test file not found: {tests_path}")
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("coding_agents_tests", tests_path)
    if spec is None or spec.loader is None:
        print(f"Could not load tests from: {tests_path}")
        sys.exit(1)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import unittest

    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if suite.countTestCases() == 0:
        print("No tests were discovered in tests.py")
        sys.exit(1)
    if not result.wasSuccessful():
        sys.exit(1)


USAGE = """
Multi-Agent Coding Usage Monitor

Usage:
  python main.py scan [--projects-dir PATH]      Scan JSONL files and update database
  python main.py rescan [--projects-dir PATH]    Rebuild the database from scratch
  python main.py today                           Show today's usage summary
  python main.py stats                           Show all-time usage statistics
  python main.py dashboard [--projects-dir PATH] Scan + start dashboard
  python main.py serve                           Start dashboard without scanning
  python main.py test                            Run tests
"""

COMMANDS = {
    "scan": cmd_scan,
    "rescan": cmd_rescan,
    "today": cmd_today,
    "stats": cmd_stats,
    "dashboard": cmd_dashboard,
    "serve": cmd_serve,
    "test": cmd_test,
}


def parse_projects_dir(args):
    for index, arg in enumerate(args):
        if arg == "--projects-dir" and index + 1 < len(args):
            return args[index + 1]
    return None


def main(argv=None):
    argv = argv or sys.argv
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(USAGE)
        return 0

    command = argv[1]
    projects_dir = parse_projects_dir(argv[2:])
    if command in ("scan", "rescan", "dashboard") and projects_dir:
        COMMANDS[command](projects_dir=projects_dir)
    else:
        COMMANDS[command]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
