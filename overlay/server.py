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
from urllib.parse import unquote, urlparse

from .config import PRESETS, apply_preset, save_config
from .names import all_names_ja

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SPRITE_DIR = ROOT / "data" / "sprites"
FONT_EXTS = {".ttf", ".otf", ".woff", ".woff2"}
# ポケモン HOME の画像 (PokeAPI がホストしているもの)。初回だけ取得して data/sprites に保存する
SPRITE_URLS = [
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/home/{id}.png",
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{id}.png",
]
log = logging.getLogger(__name__)
_sprite_lock = threading.Lock()
_sprite_failed = {}       # pid -> 最後に失敗した時刻 (失敗が続く画像を何度も取りに行かない)
_sprite_inflight = set()  # 先読み中の pid


def web_version() -> str:
    """表示用ファイル (web/) の更新時刻から作る版番号。変わったら OBS 側のページが自動で読み込み直す。"""
    import hashlib
    h = hashlib.md5()
    for f in sorted(WEB_DIR.glob("*.*")):
        h.update(f"{f.name}:{f.stat().st_mtime_ns}".encode())
    return h.hexdigest()[:10]


def prefetch_sprites(pids):
    """パーティ等で使う画像を裏で取得しておく (表示するときに待たせない・取りこぼさない)。"""
    import time
    todo = [p for p in pids if p and not (SPRITE_DIR / f"{p}.png").exists() and p not in _sprite_inflight
            and time.time() - _sprite_failed.get(p, 0) > 30]
    if not todo:
        return
    _sprite_inflight.update(todo)

    def run():
        for p in todo:
            try:
                get_sprite(p)
            finally:
                _sprite_inflight.discard(p)
    threading.Thread(target=run, daemon=True).start()


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
            _sprite_failed.pop(pid, None)
            return data
    import time
    if pid not in _sprite_failed:
        log.warning("ポケモンの画像を取得できませんでした (id=%s)。インターネット接続を確認してください。", pid)
    _sprite_failed[pid] = time.time()
    return None


WEB_VERSION = web_version()


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
                snap = app.state.snapshot(app.cfg["regions"])
                snap["display"] = app.cfg.get("display", {})
                snap["app_version"] = WEB_VERSION
                prefetch_sprites([m.get("sprite") for ms in snap["parties"].values() for m in ms]
                                 + [s.get("sprite") for s in snap["slots"]])
                return self._json(snap)
            if path == "/api/fonts":  # web/fonts に置かれたフォントファイル
                d = WEB_DIR / "fonts"
                files = sorted(f.name for f in d.iterdir() if f.suffix.lower() in FONT_EXTS) if d.exists() else []
                return self._json(files)
            if path == "/api/frame.jpg":
                jpeg = app.state.frame_jpeg()
                if jpeg is None:
                    return self._send(404, b"no frame", "text/plain")
                return self._send(200, jpeg, "image/jpeg")
            if path == "/api/config":
                return self._json({"regions": app.cfg["regions"], "usage": app.cfg["usage"],
                                   "preview": app.cfg.get("preview"), "layout": app.cfg.get("layout"),
                                   "presets": {k: v["label"] for k, v in PRESETS.items()}})
            if path == "/api/nicknames":
                from .names import resolve
                return self._json([dict(n, display=(resolve(n["ident"], app.usage) or {}).get("display", n["ident"]))
                                   for n in app.state.nicknames.list()])
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

            f = (WEB_DIR / unquote(path).lstrip("/")).resolve()  # 日本語のファイル名 (フォント等) に対応
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
                              "my_gender_first", "my_gender_last", "opp_gender_first", "opp_gender_last",
                              "my_icon_first", "my_icon_last", "opp_type1_first", "opp_type1_last",
                              "opp_type2_first", "opp_type2_last"):
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
                # 読めた文字がポケモン名でなかった (= ニックネーム) なら、そのニックネームとして覚える
                dbg = app.state.debug.get(body.get("id")) or {}
                if body.get("ident") and dbg.get("text") and dbg.get("via") in ("unknown", "portrait", "nickname"):
                    from .nicknames import looks_like_name
                    if looks_like_name(dbg["text"]):
                        app.state.label_nickname(dbg["text"], app.state.slots[body["id"]]["ident"])
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
            if path == "/api/display":
                cur = app.cfg.setdefault("display", {})
                for k in ("font", "gfont", "fontfile"):
                    if k in body:
                        v = str(body[k] or "").strip()[:100]
                        if k == "fontfile" and v and (Path(v).name != v or Path(v).suffix.lower() not in FONT_EXTS):
                            return self._json({"error": "フォントファイル名が不正です"}, 400)
                        cur[k] = v
                if "fontweight" in body:
                    w = int(body.get("fontweight") or 0)
                    cur["fontweight"] = w if 100 <= w <= 900 else 0
                save_config(app.cfg)
                return self._json({"ok": True, "display": cur})
            if path == "/api/learn_marker":  # 今の画面を選出画面として覚える
                ok = app.recognizer is not None and app.recognizer.learn_marker()
                return self._json({"ok": bool(ok)})
            if path == "/api/forget_marker":
                if app.recognizer is not None:
                    for it in app.recognizer.marker_db.list():
                        app.recognizer.marker_db.remove(it["id"])
                return self._json({"ok": True})
            if path == "/api/nickname":
                key = app.state.label_nickname(str(body.get("nick", "")), str(body.get("name", "")).strip())
                return self._json({"ok": bool(key), "key": key})
            if path == "/api/nickname_delete":
                return self._json({"ok": app.state.nicknames.remove(str(body.get("nick", "")))})
            if path == "/api/preset":
                name = body.get("name")
                if name not in PRESETS:
                    return self._json({"error": "unknown preset"}, 400)
                apply_preset(app.cfg, name)
                save_config(app.cfg)
                return self._json({"ok": True, "regions": app.cfg["regions"], "preview": app.cfg["preview"]})
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
