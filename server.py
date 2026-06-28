import json
import mimetypes
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from analytics import (
    clear_dashboard_cache,
    get_light_dashboard_data,
)
from ingest import rebuild_database, scan_all_sources
from paths import STATIC_DIR, resolve_db_path

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRONTEND_SRC_DIR = FRONTEND_DIR / "src"
FRONTEND_DIST_DIR = FRONTEND_DIR / "dist"

_SCAN_LOCK = threading.Lock()
_AUTOSCAN_THREAD_LOCK = threading.Lock()
_AUTOSCAN_THREAD = {"thread": None}
_RUNTIME_STATUS_LOCK = threading.Lock()
_RUNTIME_STATUS = {
    "auto_scan_enabled": False,
    "auto_scan_interval_seconds": 0.0,
    "scan_in_progress": False,
    "last_scan_reason": None,
    "last_scan_started_at": None,
    "last_scan_completed_at": None,
    "last_scan_result": None,
    "last_scan_error": None,
}


def _content_type_for(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".js":
        return "text/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    if suffix == ".svg":
        return "image/svg+xml"
    if suffix == ".png":
        return "image/png"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".woff":
        return "font/woff"
    if suffix == ".woff2":
        return "font/woff2"
    if suffix == ".ttf":
        return "font/ttf"
    content_type, _ = mimetypes.guess_type(path.name)
    return content_type or "application/octet-stream"


def _format_mtime(timestamp):
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_join(base: Path, rel: str):
    try:
        base_resolved = base.resolve()
        normalized_rel = (rel or "").replace("\\", "/").lstrip("/")
        target = (base_resolved / normalized_rel).resolve()
        target.relative_to(base_resolved)
        return target
    except (ValueError, OSError):
        return None


_BUILD_STATUS_CACHE = {"value": None}
_BUILD_STATUS_LOCK = threading.Lock()


def _compute_frontend_build_status():
    dist_index = FRONTEND_DIST_DIR / "index.html"
    if not dist_index.exists():
        return {
            "exists": False,
            "stale": False,
            "build_time": None,
            "latest_source_time": None,
        }

    latest_source_mtime = 0.0
    if FRONTEND_SRC_DIR.exists():
        for path in FRONTEND_SRC_DIR.rglob("*"):
            if path.is_file():
                latest_source_mtime = max(latest_source_mtime, path.stat().st_mtime)
    for path in (
        FRONTEND_DIR / "index.html",
        FRONTEND_DIR / "package.json",
        FRONTEND_DIR / "vite.config.ts",
        FRONTEND_DIR / "tsconfig.json",
        FRONTEND_DIR / "tsconfig.app.json",
        FRONTEND_DIR / "tsconfig.node.json",
    ):
        if path.exists() and path.is_file():
            latest_source_mtime = max(latest_source_mtime, path.stat().st_mtime)

    build_mtime = dist_index.stat().st_mtime
    stale = latest_source_mtime > build_mtime + 0.001
    return {
        "exists": True,
        "stale": stale,
        "build_time": _format_mtime(build_mtime),
        "latest_source_time": _format_mtime(latest_source_mtime),
    }


def get_frontend_build_status(force_refresh=False):
    """Cached variant. Expensive rglob of frontend/src runs once per process
    unless the caller passes force_refresh=True (used by /api/build-status and
    the startup banner). Rescan also triggers a refresh so the warning stays
    honest after a user rebuilds and reruns."""
    with _BUILD_STATUS_LOCK:
        if force_refresh or _BUILD_STATUS_CACHE["value"] is None:
            _BUILD_STATUS_CACHE["value"] = _compute_frontend_build_status()
        return _BUILD_STATUS_CACHE["value"]


def _update_runtime_status(**kwargs):
    with _RUNTIME_STATUS_LOCK:
        _RUNTIME_STATUS.update(kwargs)


def get_runtime_status():
    with _RUNTIME_STATUS_LOCK:
        status = dict(_RUNTIME_STATUS)
        if isinstance(status.get("last_scan_result"), dict):
            status["last_scan_result"] = dict(status["last_scan_result"])
        return status


def _auto_scan_interval_seconds():
    raw = os.environ.get("AUTO_SCAN_SECONDS", "30")
    try:
        interval = float(raw)
    except (TypeError, ValueError):
        return 30.0
    return max(0.0, interval)


def _refresh_dashboard_cache_if_needed(result):
    if not result:
        return
    if result.get("new") or result.get("updated"):
        clear_dashboard_cache()
        try:
            get_light_dashboard_data()
        except Exception:
            pass


def _run_incremental_scan(db_path, verbose=False, reason="auto-scan"):
    _update_runtime_status(
        scan_in_progress=True,
        last_scan_reason=reason,
        last_scan_started_at=_iso_now(),
        last_scan_error=None,
    )
    try:
        with _SCAN_LOCK:
            result = scan_all_sources(db_path=db_path, verbose=verbose)
        _refresh_dashboard_cache_if_needed(result)
        _update_runtime_status(
            scan_in_progress=False,
            last_scan_completed_at=_iso_now(),
            last_scan_result=result,
            last_scan_error=None,
        )
        return result
    except Exception as exc:
        _update_runtime_status(
            scan_in_progress=False,
            last_scan_completed_at=_iso_now(),
            last_scan_error=str(exc),
        )
        raise


def _run_full_rescan(db_path, verbose=False, reason="manual-rescan"):
    _update_runtime_status(
        scan_in_progress=True,
        last_scan_reason=reason,
        last_scan_started_at=_iso_now(),
        last_scan_error=None,
    )
    try:
        with _SCAN_LOCK:
            result = rebuild_database(db_path=db_path, verbose=verbose)
        clear_dashboard_cache()
        get_frontend_build_status(force_refresh=True)
        try:
            get_light_dashboard_data()
        except Exception:
            pass
        _update_runtime_status(
            scan_in_progress=False,
            last_scan_completed_at=_iso_now(),
            last_scan_result=result,
            last_scan_error=None,
        )
        return result
    except Exception as exc:
        _update_runtime_status(
            scan_in_progress=False,
            last_scan_completed_at=_iso_now(),
            last_scan_error=str(exc),
        )
        raise


def _autoscan_loop(db_path, interval_seconds):
    while True:
        try:
            result = _run_incremental_scan(db_path=db_path, verbose=False, reason="auto-scan")
            if result.get("new") or result.get("updated"):
                print(
                    f"[auto-scan] new={result['new']} updated={result['updated']} "
                    f"skipped={result['skipped']} turns={result['turns']}"
                )
        except Exception as exc:
            print(f"[auto-scan] failed: {exc}")
        time.sleep(interval_seconds)


def start_background_scanner(db_path=None):
    interval_seconds = _auto_scan_interval_seconds()
    _update_runtime_status(
        auto_scan_enabled=interval_seconds > 0,
        auto_scan_interval_seconds=interval_seconds,
    )
    if interval_seconds <= 0:
        return interval_seconds

    with _AUTOSCAN_THREAD_LOCK:
        thread = _AUTOSCAN_THREAD.get("thread")
        if thread and thread.is_alive():
            return interval_seconds
        target_db = Path(db_path or resolve_db_path())
        thread = threading.Thread(
            target=_autoscan_loop,
            args=(target_db, interval_seconds),
            name="burnmap-auto-scan",
            daemon=True,
        )
        _AUTOSCAN_THREAD["thread"] = thread
        thread.start()
    return interval_seconds


def parse_filters(path):
    query = parse_qs(urlsplit(path).query)
    range_name = query.get("range", [None])[0]
    models_param = query.get("models", [""])[0]
    providers_param = query.get("providers", [""])[0]
    from_date = query.get("from", [None])[0]
    to_date = query.get("to", [None])[0]
    if models_param == "__none__":
        models = []
    else:
        models = [model for model in models_param.split(",") if model] or None
    if providers_param == "__none__":
        providers = []
    else:
        providers = [provider for provider in providers_param.split(",") if provider] or None
    return range_name, models, providers, from_date, to_date


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _is_loopback_client(self):
        return self.client_address and self.client_address[0] in ("127.0.0.1", "::1", "localhost")

    def _send_bytes(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, relpath):
        path = _safe_join(STATIC_DIR, relpath)
        if path is None or not path.exists() or not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        self._send_bytes(200, path.read_bytes(), _content_type_for(path))

    def _serve_frontend_dist(self, relpath="index.html"):
        if not FRONTEND_DIST_DIR.exists():
            return False
        rel = (relpath or "index.html").lstrip("/") or "index.html"
        target = _safe_join(FRONTEND_DIST_DIR, rel)
        if target is not None and target.exists() and target.is_file():
            self._send_bytes(200, target.read_bytes(), _content_type_for(target))
            return True
        index_path = FRONTEND_DIST_DIR / "index.html"
        if index_path.exists():
            self._send_bytes(200, index_path.read_bytes(), "text/html; charset=utf-8")
            return True
        return False

    def _serve_frontend_asset(self, relpath):
        if not FRONTEND_DIST_DIR.exists():
            self.send_response(404)
            self.end_headers()
            return
        target = _safe_join(FRONTEND_DIST_DIR, relpath or "")
        if target is not None and target.exists() and target.is_file():
            self._send_bytes(200, target.read_bytes(), _content_type_for(target))
            return
        self.send_response(404)
        self.end_headers()

    def _serve_frontend_preview(self):
        if self._serve_frontend_dist("index.html"):
            return

        html = """<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"UTF-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>React Frontend Preview</title>
<style>
body { font-family: Inter, Arial, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:40px; }
.card { max-width:900px; margin:0 auto; background:#111827; border:1px solid #334155; border-radius:16px; padding:28px; }
h1 { margin-top:0; }
code, pre { background:#020617; border:1px solid #334155; border-radius:8px; padding:2px 6px; }
pre { padding:14px; overflow:auto; }
a { color:#93c5fd; }
</style>
</head>
<body>
<div class=\"card\">
<h1>React frontend build not found</h1>
<p>This dashboard now serves the React frontend at <code>/</code>. Build <code>frontend/dist/</code> and reload the page.</p>
<pre>cd frontend
npm install
npm run build</pre>
<p>Then reload <a href=\"/\">/</a>. Legacy <code>/app/</code> URLs redirect to <code>/</code>.</p>
</div>
</body>
</html>"""
        self._send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/app", "/app/"):
            self.send_response(301)
            self.send_header("Location", "/")
            self.end_headers()
        elif path.startswith("/app/"):
            self.send_response(301)
            self.send_header("Location", "/" + path[len("/app/"):])
            self.end_headers()
        elif path in ("/", "/index.html"):
            if self._serve_frontend_dist("index.html"):
                return
            self._serve_frontend_preview()
        elif path.startswith("/assets/"):
            self._serve_frontend_asset(path.lstrip("/"))
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
        elif path == "/api/data":
            range_name, models, providers, from_date, to_date = parse_filters(self.path)
            body = json.dumps(get_light_dashboard_data(range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date)).encode("utf-8")
            self._send_bytes(200, body, "application/json")
        elif path == "/api/session-detail":
            range_name, models, providers, from_date, to_date = parse_filters(self.path)
            query = parse_qs(urlsplit(self.path).query)
            from analytics import get_session_detail_data
            session_id = query.get("id", [""])[0]
            body = json.dumps(get_session_detail_data(range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date, session_id=session_id)).encode("utf-8")
            self._send_bytes(200, body, "application/json")
        elif path == "/api/project-detail":
            range_name, models, providers, from_date, to_date = parse_filters(self.path)
            query = parse_qs(urlsplit(self.path).query)
            from analytics import get_project_detail_data
            project_name = query.get("name", [""])[0]
            body = json.dumps(get_project_detail_data(range_name=range_name, models=models, providers=providers, from_date=from_date, to_date=to_date, project_name=project_name)).encode("utf-8")
            self._send_bytes(200, body, "application/json")
        elif path == "/api/build-status":
            status = get_frontend_build_status(force_refresh=True)
            body = json.dumps(status).encode("utf-8")
            self._send_bytes(200, body, "application/json")
        elif path == "/api/runtime-status":
            body = json.dumps(get_runtime_status()).encode("utf-8")
            self._send_bytes(200, body, "application/json")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/rescan":
            if not self._is_loopback_client():
                body = json.dumps({"error": "Rescan is only allowed from localhost."}).encode("utf-8")
                self._send_bytes(403, body, "application/json")
                return
            db_path = resolve_db_path()
            try:
                result = _run_full_rescan(db_path=db_path, verbose=False, reason="manual-rescan")
            except Exception as exc:
                body = json.dumps({"error": f"Rescan failed: {exc}"}).encode("utf-8")
                self._send_bytes(500, body, "application/json")
                return
            body = json.dumps(result).encode("utf-8")
            self._send_bytes(200, body, "application/json")
        else:
            self.send_response(404)
            self.end_headers()


def serve(host=None, port=None):
    host = host or os.environ.get("HOST", "localhost")
    port = port or int(os.environ.get("PORT", "8080"))
    db_path = resolve_db_path()
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    status = get_frontend_build_status(force_refresh=True)
    auto_scan_seconds = start_background_scanner(db_path=db_path)
    print(f"Dashboard running at http://{host}:{port}/")
    print("Canonical app route: /")
    print("Legacy route compatibility: /app/ -> /")
    if status["exists"]:
        print(f"Frontend build: frontend/dist/index.html ({status['build_time']})")
        if status["stale"]:
            print("WARNING: frontend/dist appears older than frontend source files.")
            print("Run: cd frontend && npm run build")
    else:
        print("Frontend build: missing frontend/dist/index.html")
        print("Build with: cd frontend && npm install && npm run build")
    if auto_scan_seconds > 0:
        print(f"Background live scan: every {auto_scan_seconds:g}s (set AUTO_SCAN_SECONDS=0 to disable)")
    else:
        print("Background live scan: disabled (AUTO_SCAN_SECONDS=0)")
    if host not in ("localhost", "127.0.0.1", "::1"):
        print("WARNING: dashboard is exposed beyond localhost and has no authentication.")
        print("Only /api/rescan is restricted to loopback clients.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    serve()
