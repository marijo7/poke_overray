"""オーバーレイ (OBS ブラウザソース) と設定画面を配信するローカル Web サーバー。"""
import io
import json
import logging
import mimetypes
import re
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import save_config
from .names import all_names_ja

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SPRITE_DIR = ROOT / "data" / "sprites"
# ポケモン HOME の画像 (PokeAPI がホストしているもの)。初回だけ取得して data/sprites に保存する
SPRITE_URLS = [
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/home/{id}.png",
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{id}.png",
]
log = logging.getLogger(__name__)
_sprite_lock = threading.Lock()


def get_sprite(pid: int):
    path = SPRITE_DIR / f"{pid}.png"
    if path.exists():
        return path.read_bytes()
    with _sprite_lock:
        if path.exists():
            return path.read_bytes()
        for url in SPRITE_URLS:
            try:
                req = urllib.request.Request(url.format(id=pid), headers={"User-Agent": "poke-overlay/1.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
            except Exception as e:
                log.debug("画像取得失敗 %s: %s", url.format(id=pid), e)
                continue
            SPRITE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return data
    return None


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

        def _icon_dbs(self):
            """選出画面のアイコン / 対戦中の顔画像 (メガシンカ判定用)"""
            dbs = [("preview", app.state.icons)]
            if app.recognizer is not None:
                dbs.append(("portrait", app.recognizer.portraits))
            return dbs

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/overlay"):
                path = "/overlay.html"
            elif path in ("/control", "/party"):
                path += ".html"

            if path == "/api/state":
                return self._json(app.state.snapshot(app.cfg["regions"]))
            if path == "/api/frame.jpg":
                jpeg = app.state.frame_jpeg()
                if jpeg is None:
                    return self._send(404, b"no frame", "text/plain")
                return self._send(200, jpeg, "image/jpeg")
            if path == "/api/config":
                return self._json({"regions": app.cfg["regions"], "usage": app.cfg["usage"],
                                   "preview": app.cfg.get("preview")})
            if path == "/api/pokemon":
                return self._json([{"key": e["key"], "display": e["display"], "rank": e.get("rank")}
                                   for e in app.usage.list()])
            if path == "/api/names":  # 手動入力の候補 (採用率データがあるもの → 全ポケモン)
                seen, out = set(), []
                for n in [e["display"] for e in app.usage.list()] + all_names_ja():
                    if n not in seen:
                        seen.add(n)
                        out.append(n)
                return self._json(out)
            if path == "/api/natures":  # 性格の手入力: 候補一覧と、入力済みの内容
                from .names import nature_list_ja
                return self._json({"natures": nature_list_ja(), "data": app.usage.manual_natures})
            if path == "/api/icons":
                from .names import resolve
                out = []
                for kind, db in self._icon_dbs():
                    out += [{"id": it["id"], "key": it["key"], "kind": kind,
                             "display": (resolve(it["key"], app.usage) or {}).get("display", it["key"])}
                            for it in db.list()]
                return self._json(out)
            m = re.fullmatch(r"/api/icon/([A-Za-z0-9_]+)\.png", path)
            if m:  # 選出画面で切り出したアイコン / 登録済みアイコン
                uid = m.group(1)
                crop = app.state.icon_crops.get(uid)
                if crop is not None:
                    buf = io.BytesIO()
                    crop.save(buf, "PNG")
                    return self._send(200, buf.getvalue(), "image/png")
                for _, db in self._icon_dbs():
                    f = db.image_path(uid)
                    if f:
                        return self._send(200, f.read_bytes(), "image/png")
                return self._send(404, b"no icon", "text/plain")
            m = re.fullmatch(r"/sprite/(\d+)\.png", path)
            if m:
                data = get_sprite(int(m.group(1)))
                if data is None:
                    return self._send(404, b"no sprite", "text/plain")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "max-age=86400")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

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
                pv = body.get("preview")
                if isinstance(pv, dict):
                    cur = app.cfg.setdefault("preview", {})
                    for k in ("marker", "opp_first", "opp_last", "my_first", "my_last",
                              "my_gender_first", "my_gender_last", "opp_gender_first", "opp_gender_last"):
                        if isinstance(pv.get(k), dict):
                            cur[k] = {c: min(1.0, max(0.0, float(pv[k][c]))) for c in ("x", "y", "w", "h")}
                    if "enabled" in pv:
                        cur["enabled"] = bool(pv["enabled"])
                clean = []
                for r in regions:
                    item = {"id": str(r["id"]), "label": str(r.get("label", r["id"])),
                            "side": "me" if r.get("side") == "me" else "opp",
                            **{k: min(1.0, max(0.0, float(r[k]))) for k in ("x", "y", "w", "h")}}
                    for sub in ("portrait", "gender"):  # 顔画像 / 性別マークの範囲
                        if isinstance(r.get(sub), dict):
                            item[sub] = {k: min(1.0, max(0.0, float(r[sub][k]))) for k in ("x", "y", "w", "h")}
                    clean.append(item)
                app.cfg["regions"] = clean
                save_config(app.cfg)
                return self._json({"ok": True, "regions": clean, "preview": app.cfg.get("preview")})
            if path == "/api/slot":
                region = next((r for r in app.cfg["regions"] if r["id"] == body.get("id")), {})
                app.state.set_slot(body.get("id"), ident=body.get("ident"), side=region.get("side", "opp"),
                                   source="manual")
                if body.get("ident") and app.recognizer is not None:
                    app.recognizer.learn_portrait(body.get("id"), app.state.slots[body["id"]]["ident"])
                return self._json({"ok": True})
            if path == "/api/party":
                side = body.get("side")
                members = body.get("members")
                if side not in ("me", "opp") or not isinstance(members, list):
                    return self._json({"error": "side と members が必要"}, 400)
                members = [str(m).strip() for m in members if str(m).strip()]
                app.state.set_party(side, members)
                if side == "me":
                    app.cfg["my_party"] = app.state.parties["me"]
                    save_config(app.cfg)
                return self._json({"ok": True})
            if path == "/api/natures":
                key = str(body.get("key", ""))
                if app.usage.get(key) is None:
                    return self._json({"error": "採用率データに無いポケモンです"}, 400)
                saved = app.usage.set_natures(key, body.get("natures") or [])
                app.state.version += 1
                return self._json({"ok": True, "natures": saved})
            if path == "/api/icon_label":
                key = app.state.label_icon(str(body.get("uid", "")), str(body.get("name", "")).strip())
                return self._json({"ok": bool(key), "key": key})
            if path == "/api/icon_delete":
                return self._json({"ok": any(db.remove(str(body.get("id", ""))) for _, db in self._icon_dbs())})
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
