"""オーバーレイ (OBS ブラウザソース) と設定画面を配信するローカル Web サーバー。"""
import json
import logging
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import save_config

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger(__name__)


class App:
    def __init__(self, cfg, state, usage, recognizer=None):
        self.cfg, self.state, self.usage, self.recognizer = cfg, state, usage, recognizer


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # アクセスログは出さない
            pass

        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/overlay"):
                path = "/overlay.html"
            elif path == "/control":
                path = "/control.html"

            if path == "/api/state":
                return self._json(app.state.snapshot(app.cfg["regions"]))
            if path == "/api/frame.jpg":
                if app.state.frame_jpeg is None:
                    return self._send(404, b"no frame", "text/plain")
                return self._send(200, app.state.frame_jpeg, "image/jpeg")
            if path == "/api/config":
                return self._json({"regions": app.cfg["regions"], "usage": app.cfg["usage"]})
            if path == "/api/pokemon":
                return self._json([{"key": e["key"], "display": e["display"], "rank": e.get("rank")}
                                   for e in app.usage.list()])

            f = (WEB_DIR / path.lstrip("/")).resolve()
            if WEB_DIR not in f.parents or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return self._send(200, f.read_bytes(), ctype)

        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = self._body()
            except ValueError:
                return self._json({"error": "invalid json"}, 400)

            if path == "/api/regions":
                regions = body.get("regions")
                if not isinstance(regions, list):
                    return self._json({"error": "regions required"}, 400)
                clean = []
                for r in regions:
                    clean.append({"id": str(r["id"]), "label": str(r.get("label", r["id"])),
                                  **{k: min(1.0, max(0.0, float(r[k]))) for k in ("x", "y", "w", "h")}})
                app.cfg["regions"] = clean
                save_config(app.cfg)
                return self._json({"ok": True, "regions": clean})
            if path == "/api/slot":
                app.state.set_slot(body["id"], key=body.get("key"), species=body.get("species"), source="manual")
                return self._json({"ok": True})
            if path == "/api/reset":
                app.state.reset()
                return self._json({"ok": True})
            if path == "/api/pause":
                app.state.status["paused"] = bool(body.get("paused"))
                return self._json({"ok": True, "paused": app.state.status["paused"]})
            if path == "/api/reload_usage":
                ok = app.usage.reload()
                return self._json({"ok": ok, "count": len(app.usage.entries)})
            return self._json({"error": "not found"}, 404)

    return Handler


def serve(app: App):
    host, port = app.cfg["server"]["host"], app.cfg["server"]["port"]
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
