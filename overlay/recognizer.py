"""画面を定期的に取得し、各領域の OCR → ポケモン名特定 → 状態更新を行う。"""
import io
import logging
import os

import numpy as np
import threading
import time

from .capture import create_capture
from .config import save_config
from .champ_icons import ChampionIcons
from .icons import ICON_DIR, IconDB, crop_with_margin
from .gender import crop as crop_rect
from .gender import detect_gender
from .names import (NameMatcher, base_stats, form_abilities_ja, gender_variant, is_mega, mega_forms, normalize,
                    resolve, type_name_ja, types_of)
from .nicknames import NicknameDB, clean_nick, looks_like_name, nick_key
from .ocr import create_ocr, preprocess

log = logging.getLogger(__name__)


class State:
    """オーバーレイに配信する状態。スレッド間で共有する。"""

    PARTY_SIZE = 6

    def __init__(self, usage_db, my_party=None, icons=None, nicknames=None):
        self.usage = usage_db
        self.nicknames = nicknames if nicknames is not None else NicknameDB()
        self.unknown_nicks = {}  # nick_key -> {"nick", "side"}: 何のポケモンか分からないニックネーム
        self.icons = icons     # IconDB (選出画面のアイコン照合)
        self.icon_crops = {}   # 未登録アイコンの切り出し画像 uid -> PIL.Image
        self.type_crops = {}   # uid -> ((左のタイプ 余白付き, 余白なし), (右のタイプ 余白付き, 余白なし))
        self.type_db = IconDB(ICON_DIR.parent / "types") if icons is not None else None  # タイプアイコン (学習)
        self.icon_guess = {}   # uid -> {"types": [...], "cands": [...], "guess": キー or None}: タイプからの推定
        self.lock = threading.Lock()
        self.slots = {}        # region id -> {"ident", "species", "side", "source", "updated"}
        # パーティ (識別子 = 採用率データのキー or 日本語名)。自分は config に保存、相手は対戦ごとにリセット
        self.parties = {"me": list(my_party or [])[: self.PARTY_SIZE], "opp": []}
        self.base_my = list(self.parties["me"])  # メガシンカ表示を戻すための元の自分パーティ
        self.debug = {}        # region id -> {"text", "match", "score"}
        self.status = {"capture": "未接続", "ocr": "-", "error": None, "fps": 0.0, "paused": False}
        # 負荷: 取得/OCR にかかった時間 (ms, 移動平均) と、このプログラム全体の CPU 使用率 (PC 全体に対する %)
        self.perf = {"grab_ms": None, "ocr_ms": None, "cpu_pct": None}
        self.frame = None
        self.version = 0

    def frame_jpeg(self):
        """設定画面のプレビュー用。要求されたときだけ JPEG 化する (配信中の無駄な負荷を避ける)。"""
        frame = self.frame
        if frame is None:
            return None
        thumb = frame.copy()
        thumb.thumbnail((1280, 720))
        buf = io.BytesIO()
        thumb.save(buf, "JPEG", quality=80)
        return buf.getvalue()

    def _resolve(self, ident):
        """識別子 → 表示情報。日本語の種族名なら採用率データの該当フォルムに寄せる。"""
        info = resolve(ident, self.usage)
        if info and not info["has_data"]:
            entry = self.usage.find_by_species(info["species"])
            if entry and normalize(info["display"]) == normalize(info["species"]):
                info = resolve(entry["key"], self.usage)
        return info

    def set_slot(self, region_id, ident=None, side="opp", source="auto", specific=False):
        """specific: 性別やフォルムまで確定した識別子 (パーティ内の同じ種族の表示も差し替える)"""
        with self.lock:
            if not ident:
                self.slots.pop(region_id, None)
            else:
                info = self._resolve(ident)
                self.slots[region_id] = {"ident": info["key"], "species": info["species"], "side": side,
                                         "source": source, "updated": time.time()}
                if is_mega(info["key"]) or specific:  # メガシンカ後・性別違いはパーティ表示も差し替える
                    mine = self.parties[side]
                    for i, m in enumerate(mine):
                        if self._resolve(m)["species"] == info["species"]:
                            mine[i] = info["key"]
                party = self.parties["opp"]
                if side == "opp":  # タイプから推定したポケモンが実際に出てきたら、そのアイコンとして覚える
                    for uid, g in list(self.icon_guess.items()):
                        if g.get("guess") and self._resolve(g["guess"])["species"] == info["species"]:
                            self._label_icon_locked(uid, g["guess"])
                            log.info("推定が当たったのでアイコンを登録: %s", info["display"])
                if side == "opp" and all(self._resolve(m)["species"] != info["species"] for m in party):
                    unknown = [m for m in party if m.startswith("icon:")]
                    if len(party) >= self.PARTY_SIZE and len(unknown) == 1:
                        # 選出画面の未登録アイコンが残り 1 つ → それがこのポケモンだと分かるので自動で覚える
                        self._label_icon_locked(unknown[0][5:], info["key"])
                        log.info("アイコンを自動登録: %s", info["display"])
                    elif len(party) < self.PARTY_SIZE:
                        party.append(info["key"])
            self.version += 1

    def learn_types(self, uid, key):
        """名前の分かったアイコンの横のタイプアイコンを、そのタイプの見た目として覚える (1 タイプ 3 枚まで)。"""
        crops, types = self.type_crops.get(uid), types_of(key, self.usage)
        if not crops or not types or self.type_db is None:
            return
        # 2 タイプなら左=1 つ目・右=2 つ目、1 タイプなら右だけに出る
        pairs = list(zip(crops, types)) if len(types) == 2 else [(crops[1], types[0])]
        for (outer, inner), t in pairs:
            n = sum(1 for it in self.type_db.list() if it["key"] == t)
            if n >= 3:
                continue
            if n:
                _, score = self.type_db.match(outer, keys={t})
                if score >= 0.92:
                    continue  # もう同じ見た目を覚えている
            self.type_db.add(inner, t)
            log.info("タイプアイコンを記憶: %s", type_name_ja(t))

    def _label_icon_locked(self, uid, key):
        crop = self.icon_crops.get(uid)
        if crop is not None and self.icons is not None:
            self.icons.add(crop, key)
        self.learn_types(uid, key)
        self.icon_guess.pop(uid, None)
        self.parties["opp"] = [key if m == "icon:" + uid else m for m in self.parties["opp"]]

    def add_unknown_nick(self, nick, side, cands=None):
        """cands: 絵から見て近いポケモン (設定画面で候補のボタンにする)"""
        with self.lock:
            item = self.unknown_nicks.setdefault(nick_key(nick), {"nick": clean_nick(nick), "side": side, "cands": []})
            if cands:
                item["cands"] = list(cands)

    def label_nickname(self, nick, name):
        """ニックネームに対応するポケモンを登録し、パーティ内の仮表示 (nick:) を置き換える。戻り値: 登録したキー"""
        with self.lock:
            info = self._resolve(name)
            if not info or not info.get("species"):
                return None
            key = info["key"]
            self.nicknames.set(nick, key)
            k = nick_key(nick)
            self.unknown_nicks.pop(k, None)
            for side in ("me", "opp"):
                self.parties[side] = [key if (m.startswith("nick:") and nick_key(m[5:]) == k) else m
                                      for m in self.parties[side]]
            self.base_my = [key if (m.startswith("nick:") and nick_key(m[5:]) == k) else m for m in self.base_my]
            self.version += 1
            return key

    def label_icon(self, uid, name):
        """未登録アイコンに名前を付けて登録する。戻り値: 登録したキー"""
        with self.lock:
            if uid not in self.icon_crops:
                return None
            key = self._resolve(name)["key"]
            self._label_icon_locked(uid, key)
            self.version += 1
            return key

    def set_party(self, side, members):
        with self.lock:
            self.parties[side] = [m for m in members if m][: self.PARTY_SIZE * 2]
            if side == "me":
                self.base_my = list(self.parties["me"])
            self.version += 1

    def set_form(self, region_id, form_key):
        """メガシンカ等で場のポケモンの姿が変わった。スロットとパーティの表示を差し替える。"""
        with self.lock:
            slot = self.slots.get(region_id)
            if not slot or slot["ident"] == form_key:
                return
            slot["ident"] = form_key
            party = self.parties[slot["side"]]
            for i, m in enumerate(party):
                if self._resolve(m)["species"] == slot["species"]:
                    party[i] = form_key
            self.version += 1

    def reset(self):
        with self.lock:
            self.slots.clear()
            self.parties["opp"].clear()
            self.parties["me"] = list(self.base_my)  # メガシンカ表示を元に戻す
            self.icon_crops.clear()
            self.type_crops.clear()
            self.icon_guess.clear()
            self.unknown_nicks.clear()  # 未登録ニックネームの入力欄も対戦ごとに消す (登録済みのニックネームは残す)
            self.version += 1

    def snapshot(self, regions):
        with self.lock:
            slots = []
            for r in regions:
                s = self.slots.get(r["id"])
                info = self._resolve(s["ident"]) if s else None
                slots.append({
                    "id": r["id"], "label": r.get("label", r["id"]), "side": r.get("side", "opp"),
                    "species": s["species"] if s else None,
                    "display": info["display"] if info else None,
                    "sprite": info["sprite"] if info else None,
                    "source": s["source"] if s else None,
                    # メガシンカ後も採用率は元のポケモンのデータ (メガストーンの持ち物として集計されている)
                    "pokemon": (self.usage.get(info["key"]) or self.usage.find_by_species(info["species"])) if info else None,
                    "mega": is_mega(info["key"]) if info else False,
                    "form_abilities": form_abilities_ja(info["key"]) if info and is_mega(info["key"]) else [],
                    # 種族値は今の姿 (メガシンカ後ならメガシンカ後) のもの
                    "stats": base_stats(info["sprite"]) if info else None,
                    "debug": self.debug.get(r["id"]),
                })
            parties = {}
            guessed = {g["guess"] for g in self.icon_guess.values() if g.get("guess")}
            for side, members in self.parties.items():
                active = {s["species"] for s in self.slots.values() if s["side"] == side}
                parties[side] = [dict(self._resolve(m), active=self._resolve(m)["species"] in active,
                                      guess=side == "opp" and m in guessed)
                                 for m in members]
            guesses = {uid: {"types": [type_name_ja(t) for t in g["types"]],
                             "cands": [{"key": c, "display": self._resolve(c)["display"]} for c in g["cands"][:6]],
                             "guess": g.get("guess")}
                       for uid, g in self.icon_guess.items()}
            unknown = [dict(v, cands=[{"key": c, "display": self._resolve(c)["display"]} for c in v.get("cands", [])])
                       for v in self.unknown_nicks.values()]
            dbg = {k: v for k, v in self.debug.items() if k == "preview" or k.startswith(("my", "icon"))}
            return {"version": self.version, "slots": slots, "parties": parties, "unknown_nicks": unknown,
                    "preview_debug": dbg,
                    "icon_guesses": guesses,
                    "history": parties["opp"],  # 旧形式との互換
                    "status": dict(self.status), "perf": dict(self.perf), "usage_meta": self.usage.meta}


class Recognizer(threading.Thread):
    def __init__(self, state: State, get_config):
        super().__init__(daemon=True)
        self.state = state
        self.get_config = get_config
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self.pending = {}  # region id -> (species, 連続回数)
        self.last_preview_seen = 0.0
        self.battle_no = 0
        self.preview_opp, self.preview_my = [], []
        # 体力バー横の顔画像 (メガシンカ判定用)。種族ごとに「通常」と「メガシンカ後」の見た目を覚える
        self.portraits = IconDB(ICON_DIR.parent / "portraits")
        self.portrait_pending = {}  # region id -> (候補フォルム, 連続回数)
        self.champ = ChampionIcons()  # Champions のポケモン画像 (選出画面のアイコンの推定用)
        self.pending_nick = {}      # region id -> 未登録ニックネームを続けて読んだ回数
        self.layout_votes = {}      # 他の画面レイアウトで選出画面が読めた回数
        self.battle_seen = False    # 前の選出画面のあと、対戦画面でポケモンを読み取ったか (= 次の選出画面は新しい対戦)
        self.my_icon_cache = {}     # 選出画面の自分のポケモンの絵の判定結果 (対戦ごと)
        self.last_champ_margin = 0.0

    def _setup(self):
        cfg = self.get_config()
        self.capture = create_capture(cfg["capture"])
        self.ocr = create_ocr(cfg["ocr"])
        self.matcher = NameMatcher(min_ratio=cfg.get("min_ratio", 0.72))
        self.state.status.update(capture=self.capture.describe(), ocr=self.ocr.name, error=None)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self._setup()
                self.reload_event.clear()
                self._loop()
            except Exception as e:  # 設定ミス・OBS 未起動など。少し待って再試行
                log.warning("認識エラー: %s", e)
                self.state.status["error"] = str(e)
                self.stop_event.wait(3)

    def _loop(self):
        last = time.time()
        cpu_wall, cpu_proc = time.perf_counter(), time.process_time()
        ncpu = os.cpu_count() or 1
        perf = self.state.perf
        while not self.stop_event.is_set() and not self.reload_event.is_set():
            cfg = self.get_config()
            if self.state.status.get("paused"):
                self.stop_event.wait(0.5)
                continue
            t0 = time.perf_counter()
            frame = self.capture.grab()
            t1 = time.perf_counter()
            self.state.status["error"] = None
            self.process_frame(frame, cfg)
            t2 = time.perf_counter()
            self.state.frame = frame

            perf["grab_ms"] = _ema(perf["grab_ms"], (t1 - t0) * 1000)
            perf["ocr_ms"] = _ema(perf["ocr_ms"], (t2 - t1) * 1000)
            if t2 - cpu_wall >= 2.0:  # 2 秒ごとに CPU 使用率を更新
                pt = time.process_time()
                perf["cpu_pct"] = round((pt - cpu_proc) / (t2 - cpu_wall) / ncpu * 100, 1)
                cpu_wall, cpu_proc = t2, pt

            now = time.time()
            self.state.status["fps"] = round(1 / max(now - last, 1e-3), 1)
            last = now
            self.stop_event.wait(max(0.0, cfg.get("interval_sec", 0.5) - (t2 - t0)))

    def _ocr_rect(self, frame, rect, scale):
        W, H = frame.size
        box = (int(rect["x"] * W), int(rect["y"] * H), int((rect["x"] + rect["w"]) * W), int((rect["y"] + rect["h"]) * H))
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return []
        return self.ocr.recognize(preprocess(frame.crop(box), scale))

    def process_frame(self, frame, cfg):
        scale = cfg["ocr"].get("scale", getattr(self.ocr, "default_scale", 2.0))
        pv = cfg.get("preview") or {}
        if pv.get("enabled"):
            text = "".join(self._ocr_rect(frame, pv["marker"], scale))
            keys = pv.get("keywords", ["選出"])
            found = any(k in normalize(text) or k in text for k in keys)
            if not found:
                found = self.try_other_layouts(frame, cfg, keys, scale)  # スマホ版/Switch 版の設定違いなら自動で切り替え
                pv = cfg.get("preview") or {}
            self.state.debug["preview"] = {"text": text, "found": found, "time": time.time(),
                                           "layout": cfg.get("layout")}
            if found:
                self.process_preview(frame, cfg, pv, scale)
                return
        self.process_battle(frame, cfg, scale)

    def opp_icons_changed(self, frame, pv, n):
        """選出画面の相手アイコンが、今の対戦で見たものと半分以上違うか (= 別の相手との対戦)。"""
        prev = [self.state.icon_crops.get(f"b{self.battle_no}_{i}") for i in range(n)]
        if not any(p is not None for p in prev) or not pv.get("opp_first"):
            return False
        from .icons import template_feature, window_features
        diff = 0
        for i, p in enumerate(prev):
            if p is None:
                continue
            outer, _ = crop_with_margin(frame, _lerp(pv["opp_first"], pv["opp_last"], i, n))
            if float((window_features(outer) @ template_feature(p)).max()) < 0.8:
                diff += 1
        return diff >= 3

    def try_other_layouts(self, frame, cfg, keys, scale):
        """今の設定 (例: スマホ版) では選出画面の文字が読めないとき、他の画面 (Switch 版) の位置でも読んでみる。
        2 回続けてそちらで読めたら、読み取り範囲をその画面用に切り替える。"""
        from .config import PRESETS, apply_preset
        for name, preset in PRESETS.items():
            if name == cfg.get("layout"):
                continue
            text = "".join(self._ocr_rect(frame, preset["preview"]["marker"], scale))
            if any(k in normalize(text) or k in text for k in keys):
                n = self.layout_votes.get(name, 0) + 1
                self.layout_votes = {name: n}
                if n >= 2:
                    log.info("画面が %s のようなので、読み取り範囲を %s 用に切り替えました", preset["label"], preset["label"])
                    apply_preset(cfg, name)
                    save_config(cfg)
                    self.state.status["layout_switched"] = preset["label"]
                    self.layout_votes = {}
                    return True
                return False
        self.layout_votes = {}
        return False

    def process_preview(self, frame, cfg, pv, scale):
        n = int(pv.get("count", 6))
        now = time.time()
        # 新しい対戦か? (どれか 1 つでも当てはまれば、表示とパーティを入れ替える)
        reason = None
        if now - self.last_preview_seen > 30:
            reason = "しばらくぶりの選出画面"
        elif self.battle_seen:
            reason = "対戦画面のあとの選出画面"
        elif self.opp_icons_changed(frame, pv, n):
            reason = "相手のアイコンが前の対戦と違う"
        if reason:
            log.info("選出画面を検出: 新しい対戦 (%s)", reason)
            self.state.reset()
            self.pending.clear()
            self.battle_no += 1
            self.preview_opp, self.preview_my = [None] * n, [None] * n
            self.my_icon_cache = {}
            self.state.status["battle"] = f"{self.battle_no} 戦目 ({reason})"
        self.battle_seen = False
        self.last_preview_seen = now
        self.state.status["screen"] = "選出画面"

        # 相手: アイコン照合
        thr = float(pv.get("icon_threshold", 0.8))
        for i in range(n):
            outer, inner = crop_with_margin(frame, _lerp(pv["opp_first"], pv["opp_last"], i, n))
            uid = f"b{self.battle_no}_{i}"
            self.state.icon_crops[uid] = inner
            if pv.get("opp_type1_first"):
                self.state.type_crops[uid] = tuple(
                    crop_with_margin(frame, _lerp(pv[f"opp_type{k}_first"], pv[f"opp_type{k}_last"], i, n))
                    for k in (1, 2))
            cur = self.preview_opp[i]
            if cur and not cur.startswith("icon:") and uid not in self.state.icon_guess:
                continue  # 特定済み
            key, score = self.state.icons.match(outer) if self.state.icons else (None, 0.0)
            self.state.debug[f"icon{i}"] = {"match": key, "score": round(score, 3)}
            if key and score >= thr and pv.get("opp_gender_first"):
                g = detect_gender(crop_rect(frame, _lerp(pv["opp_gender_first"], pv["opp_gender_last"], i, n)))
                key = gender_variant(resolve(key, self.state.usage)["species"], g, self.state.usage) or key
            if key and score >= thr:
                self.preview_opp[i] = key
                self.state.icon_guess.pop(uid, None)
                self.state.learn_types(uid, key)
            elif uid in self.state.icon_guess:
                pass  # 推定済み (毎フレーム推定し直さない)
            else:
                self.preview_opp[i] = self.guess_by_types(frame, pv, i, n, uid) or "icon:" + uid
            g = self.state.icon_guess.get(uid)
            if g:  # 設定画面の「選出画面の読み取り結果」用
                self.state.debug[f"icon{i}"]["guess"] = resolve(g["guess"], self.state.usage)["display"] if g.get("guess") else None
                self.state.debug[f"icon{i}"]["cands"] = len(g.get("cands") or [])
        opp = [m for m in self.preview_opp if m]
        if opp != self.state.parties["opp"]:
            # 手動で名前を付けた分 (icon: → キー) は残す
            merged = [s if (p and p.startswith("icon:") and s and not s.startswith("icon:")) else p
                      for p, s in zip(opp, self.state.parties["opp"] + [None] * n)]
            self.preview_opp = merged
            self.state.set_party("opp", merged)

        # 自分: 名前を OCR
        if pv.get("auto_my_party", True):
            for i in range(n):
                if self.preview_my[i] and not self.preview_my[i].startswith("nick:"):
                    continue  # 特定済み (未登録ニックネームは、登録されたら性別も含めて決め直す)
                texts = self._ocr_rect(frame, _lerp(pv["my_first"], pv["my_last"], i, n), scale)
                text = " ".join(texts)
                sp, _ = self.matcher.match(text) if texts else (None, 0)
                self.state.debug[f"my{i}"] = {"text": text, "match": sp}  # 設定画面の「選出画面の読み取り結果」用
                g = (detect_gender(crop_rect(frame, _lerp(pv["my_gender_first"], pv["my_gender_last"], i, n)))
                     if pv.get("my_gender_first") else None)
                if not sp and texts:
                    nick = self.state.nicknames.match(text)  # 登録済みのニックネーム
                    if nick:
                        nsp = resolve(nick, self.state.usage)["species"]
                        self.preview_my[i] = gender_variant(nsp, g, self.state.usage) or nick
                        continue
                    if looks_like_name(text):
                        # 未登録のニックネーム → 名前の右の絵を Champions の画像と比べる。はっきり分かれば自動で登録
                        guess, cands = self.my_icon_guess(frame, pv, i, n)
                        if guess:
                            key = gender_variant(resolve(guess, self.state.usage)["species"], g, self.state.usage) or guess
                            self.state.nicknames.set(text, key, auto=True)
                            self.preview_my[i] = key
                            log.info("ニックネーム「%s」を絵から %s と判定して登録", clean_nick(text),
                                     resolve(key, self.state.usage)["display"])
                            continue
                        self.preview_my[i] = "nick:" + clean_nick(text)  # 分からなければ仮表示して、設定画面で選んでもらう
                        self.state.add_unknown_nick(text, "me", cands)
                        continue
                if sp:
                    sp = gender_variant(sp, g, self.state.usage) or sp
                self.preview_my[i] = sp
            mine = [m for m in self.preview_my if m]
            if len(mine) >= n - 2 and mine != self.state.parties["me"]:
                log.info("自分のパーティを登録: %s", "、".join(mine))
                self.state.set_party("me", mine)
                cfg["my_party"] = mine
                save_config(cfg)

    def my_icon_guess(self, frame, pv, i, n):
        """選出画面の自分のポケモンの絵を、Champions の全ポケモンの画像と比べる (色+輪郭。色違いにも強い)。
        戻り値: (はっきり 1 匹に決まればそのキー, 候補の上位)。1 回の対戦で 1 度だけ計算する。"""
        if i in self.my_icon_cache:
            return self.my_icon_cache[i]
        res = (None, [])
        if pv.get("my_icon_first") and self.champ.available():
            crop = crop_rect(frame, _lerp(pv["my_icon_first"], pv["my_icon_last"], i, n))
            scored = [((c + e) / 2, k) for k, c, e in self.champ.rank(crop, self.state.usage.roster())]
            if scored:
                # 実測 (Switch 版・色違い含む): 正解は 2 位に 0.11〜0.39 の差。0.07 以下は決めない
                ok = scored[0][0] >= 0.5 and (len(scored) < 2 or scored[0][0] - scored[1][0] >= 0.10)
                res = (scored[0][1] if ok else None, [k for _, k in scored[:4]])
        self.my_icon_cache[i] = res
        return res

    def champ_key(self, member):
        """パーティの登録 (日本語名やキー) → Champions の画像のキー"""
        info = resolve(member, self.state.usage)
        if not info or not info.get("species"):
            return None
        if info["key"] in self.champ.map:
            return info["key"]
        return next((k for k in self.state.usage.roster()
                     if resolve(k, self.state.usage)["species"] == info["species"] and k in self.champ.map), None)

    def identify_by_champion(self, frame, r):
        """ニックネームで名前が分からないとき、名前の横の顔画像を、パーティのポケモンの Champions の画像と比べる。"""
        if not r.get("portrait") or not self.champ.available():
            return None
        party = self.state.parties.get(r.get("side", "opp"), [])
        if not party or any(m.startswith(("nick:", "icon:")) for m in party):
            # パーティにまだ分からないポケモンがいると、正解が候補に入っていないことがある (それを他と取り違えないよう使わない)
            return None
        keys = {self.champ_key(m) for m in party} - {None}
        if not keys:
            return None
        outer, _ = crop_with_margin(frame, r["portrait"])
        scored = sorted((((c + e) / 2, k) for k, c, e in self.champ.rank(outer, keys)), reverse=True)
        # 実測 (パーティ 6 匹の中から): 正解は 2 位に 0.06〜0.30 の差
        if scored and scored[0][0] >= 0.35 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.05):
            self.last_champ_margin = scored[0][0] - (scored[1][0] if len(scored) > 1 else 0)
            return scored[0][1]
        return None

    def read_types(self, uid):
        """タイプアイコン 2 つを、覚えたタイプアイコンと照合する。戻り値: タイプのリスト (読めなければ None)"""
        crops, db = self.state.type_crops.get(uid), self.state.type_db
        if not crops or db is None or not db.items:
            return None

        def is_empty(img):  # 1 タイプのポケモンの左側は何も無い (白い模様が無い)
            a = np.asarray(img.convert("RGB"), dtype=np.int16)
            return (a.min(axis=2) > 200).mean() < 0.03

        types = []
        for outer, inner in ([crops[1]] if is_empty(crops[0][1]) else crops):
            t, score = db.match(outer)
            if not t or score < 0.85:
                return None  # まだ覚えていないタイプ
            types.append(t)
        return types

    def guess_by_types(self, frame, pv, i, n, uid):
        """未登録アイコンを推定する。

        1. 横のタイプアイコンが読めれば、Champions で使えるポケモンを同じタイプに絞る
        2. Champions のポケモン画像があれば、アイコンの絵と比べて候補を順位付けする
           (タイプが読めないときは全ポケモンと色で比べ、はっきり一致したときだけ採用)
        候補がはっきり 1 匹に決まればそのポケモン (推定)、決まらなければ候補だけを出す。
        """
        usage = self.state.usage
        types = self.read_types(uid)
        taken = {resolve(m, usage)["species"] for m in self.preview_opp if m and not m.startswith("icon:")}
        roster, seen = [], set(taken)
        for key in usage.roster():  # 同じ種族はパーティに 1 匹だけ (性別違い等は先頭の 1 つ)
            sp = resolve(key, usage)["species"]
            if sp not in seen:
                seen.add(sp)
                roster.append(key)
        order = {k: j for j, k in enumerate(roster)}
        outer = None
        if frame is not None and pv.get("opp_first"):
            outer = crop_with_margin(frame, _lerp(pv["opp_first"], pv["opp_last"], i, n))[0]
        guess, cands, by = None, [], None
        if types:
            cands, by = [k for k in roster if types_of(k, usage) == types], "type"
            if len(cands) == 1:
                guess = cands[0]
            elif cands and outer is not None and self.champ.available(cands):
                # 色+輪郭の一致度に、使用率の高さを少しだけ足す (僅差なら使われているポケモンの方が出やすい)
                scored = sorted((((c + e) / 2 + 0.05 * (1 - order[k] / len(roster)), k)
                                 for k, c, e in self.champ.rank(outer, cands)), reverse=True)
                cands = [k for _, k in scored] + [k for k in cands if k not in {k2 for _, k2 in scored}]
                if len(scored) >= 2 and scored[0][0] - scored[1][0] >= 0.05:
                    guess, by = scored[0][1], "type+image"
        elif outer is not None and self.champ.available():
            ranked = self.champ.rank(outer, roster, use_edges=False)  # タイプ未学習: 全ポケモンと色で比べる
            cands, by = [k for k, _, _ in ranked[:5]], "image"
            # 実測: 正解は 2 位に 0.12 以上の差、外れは 0.01〜0.03 の僅差だったので、はっきりした差のときだけ採用
            if ranked and ranked[0][1] >= 0.75 and (len(ranked) < 2 or ranked[0][1] - ranked[1][1] >= 0.12):
                guess = ranked[0][0]
        if not types and not cands:
            return None  # まだ手掛かりが無い (次のフレームでもう一度)
        g = (detect_gender(crop_rect(frame, _lerp(pv["opp_gender_first"], pv["opp_gender_last"], i, n)))
             if frame is not None and pv.get("opp_gender_first") else None)
        if guess:
            guess = gender_variant(resolve(guess, usage)["species"], g, usage) or guess
        self.state.icon_guess[uid] = {"types": types or [], "cands": cands, "guess": guess, "by": by}
        log.info("相手%d: %s → 候補 %s%s", i + 1,
                 "タイプ " + "/".join(type_name_ja(t) for t in types) if types else "見た目",
                 "、".join(resolve(c, usage)["display"] for c in cands[:5]) or "なし",
                 f" → 推定 {resolve(guess, usage)['display']}" if guess else "")
        return guess

    def process_battle(self, frame, cfg, scale):
        self.state.status["screen"] = "対戦"
        W, H = frame.size
        need = max(1, int(cfg.get("confirm_frames", 2)))
        for r in cfg["regions"]:
            box = (int(r["x"] * W), int(r["y"] * H), int((r["x"] + r["w"]) * W), int((r["y"] + r["h"]) * H))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                continue
            crop = preprocess(frame.crop(box), scale)
            texts = self.ocr.recognize(crop)
            species, score, text = None, 0.0, " / ".join(texts)
            for t in texts:
                sp, sc = self.matcher.match(t)
                if sp and sc > score:
                    species, score = sp, sc
            hint, via = None, "name"
            if not species and texts and looks_like_name(text):
                # ポケモン名ではない = ニックネーム。登録済みならそれ、無ければ顔画像で判定
                hint, via = self.state.nicknames.match(text), "nickname"
                if not hint:
                    hint, via = self.identify_by_portrait(frame, r), "portrait"
                if not hint:
                    hint, via = self.identify_by_champion(frame, r), "image"
                    if hint and r.get("side") == "me" and self.last_champ_margin >= 0.10:
                        self.state.nicknames.set(text, hint, auto=True)  # はっきりしていれば自分のニックネームとして覚える
                if hint:
                    info = resolve(hint, self.state.usage)
                    species, score = info["species"], 0.9
                else:
                    self.pending_nick[r["id"]] = self.pending_nick.get(r["id"], 0) + 1
                    if self.pending_nick[r["id"]] >= need:  # 続けて読めた名前だけ未登録として出す
                        self.state.add_unknown_nick(text, r.get("side", "opp"))
            self.state.debug[r["id"]] = {"text": text, "match": species, "score": round(score, 2),
                                         "via": via if species else ("unknown" if hint is None and texts else None)}
            if not species:
                self.pending.pop(r["id"], None)
                continue
            self.pending_nick.pop(r["id"], None)
            self.battle_seen = True  # 対戦画面でポケモンを読み取った
            prev, count = self.pending.get(r["id"], (None, 0))
            count = count + 1 if prev == species else 1
            self.pending[r["id"]] = (species, count)
            current = self.state.slots.get(r["id"])
            if count >= need:
                # 性別でデータが違うポケモン (イエッサン等) は、名前の横の ♀/♂ でどちらか決める
                gender = detect_gender(crop_rect(frame, r["gender"])) if r.get("gender") else None
                gkey = gender_variant(species, gender, self.state.usage)
                if hint and not gkey and resolve(hint, self.state.usage).get("has_data"):
                    gkey = hint  # ニックネームで登録された型 (フォルム違い等) を優先
                ident = gkey or species
                changed = current is None or current["species"] != species
                regender = (not changed and gkey and current["ident"] != gkey and not is_mega(current["ident"]))
                if changed or regender:
                    log.info("%s: %s%s (%.2f) ← %r", r["id"], species, f" [{gender}]" if gkey else "", score, text)
                    self.state.set_slot(r["id"], ident=ident, side=r.get("side", "opp"), source="auto",
                                        specific=bool(gkey))
                if via in ("name", "nickname") and r.get("portrait"):
                    self.learn_base_portrait(frame, r, gkey or species)
            if r.get("portrait"):
                self.check_mega(frame, r, cfg)

    def check_mega(self, frame, r, cfg):
        """顔画像が、そのポケモンの「通常」の見た目から変わったらメガシンカと判断する。

        - 初めて場に出た種族の顔画像を「通常」として覚える (メガシンカは場に出た後なので最初は通常の姿)
        - 覚えた見た目と違う状態が数フレーム続き、その種族にメガシンカがあればメガシンカと判断
        - メガシンカ先が複数 (リザードン X/Y 等) なら、覚えたメガの見た目 → 無ければメガストーン採用率が高い方
        """
        slot = self.state.slots.get(r["id"])
        if not slot or not slot.get("species"):
            return
        species = slot["species"]
        forms = mega_forms(species)
        if not forms:
            return  # メガシンカしないポケモンは判定不要 (負荷もかけない)
        if is_mega(slot["ident"]):
            return  # メガシンカは戻らない
        outer, inner = crop_with_margin(frame, r["portrait"])
        thr = float(cfg.get("preview", {}).get("icon_threshold", 0.8))
        key, score = self.portraits.match(outer, keys={species, *forms})
        if not self.portraits.has_key(species):
            # 「通常」の見た目を覚えるのは、選出画面から見ている対戦で、場に出た直後だけ
            # (途中から起動した場合、すでにメガシンカ後の姿を通常と誤って覚えてしまうため)
            just_entered = time.time() - slot["updated"] < 5
            watched = time.time() - self.last_preview_seen < 20 * 60
            if just_entered and watched and (key is None or score < float(cfg.get("preview", {}).get("icon_threshold", 0.8))):
                self.portraits.add(inner, species)
                log.info("顔画像を記憶: %s (通常)", species)
            if key is None:
                return
        self.state.debug.setdefault(r["id"], {})["portrait"] = {"match": key, "score": round(score, 3)}
        if score >= thr:
            cand = key if key != species else None
        else:
            cand = self._guess_mega(species, forms)  # 未知の見た目 = まだ覚えていないメガシンカ後の姿
        prev, n = self.portrait_pending.get(r["id"], (None, 0))
        n = n + 1 if cand == prev else 1
        self.portrait_pending[r["id"]] = (cand, n)
        if cand and n >= 3:
            log.info("%s: メガシンカを検出 → %s (一致度 %.2f)", r["id"], cand, score)
            if score < thr and len(forms) == 1:
                self.portraits.add(inner, cand)  # メガシンカ先が 1 つなら確実なので見た目を覚える
            self.state.set_form(r["id"], cand)

    def identify_by_portrait(self, frame, r):
        """ニックネームで名前が分からないとき、名前の横の顔画像を覚えた顔と照合する。
        選出画面で相手 (自分) のパーティが分かっていれば、その中から選ぶ。"""
        if not r.get("portrait") or not self.portraits.items:
            return None
        party = [resolve(m, self.state.usage) for m in self.state.parties.get(r.get("side", "opp"), [])]
        cands = {p["species"] for p in party if p.get("species")}
        keys = None
        if cands:
            keys = {it["key"] for it in self.portraits.list()
                    if (resolve(it["key"], self.state.usage) or {}).get("species") in cands}
            if not keys:
                return None
        outer, _ = crop_with_margin(frame, r["portrait"])
        key, score = self.portraits.match(outer, keys=keys)
        thr = float(self.get_config().get("preview", {}).get("icon_threshold", 0.8))
        return key if key and score >= thr else None

    def learn_base_portrait(self, frame, r, key):
        """名前で分かったポケモンの顔画像を覚える (ニックネームのときに顔で判定するため)。
        メガシンカするポケモンは check_mega が「通常の姿」を慎重に覚えるのでここでは扱わない。
        見た目が今までと違う (状態異常のマーク等) ときは 3 枚まで追加で覚える。"""
        species = resolve(key, self.state.usage)["species"]
        if not species or mega_forms(species):
            return
        n = sum(1 for it in self.portraits.list() if it["key"] == key)
        if n >= 3:
            return
        outer, inner = crop_with_margin(frame, r["portrait"])
        if n:
            _, score = self.portraits.match(outer, keys={key})
            if score >= 0.9:
                return
        self.portraits.add(inner, key)
        log.info("顔画像を記憶: %s", key)

    def learn_portrait(self, region_id, key):
        """手動でフォルムを選んだとき、そのときの顔画像をそのフォルムの見た目として覚える。"""
        r = next((x for x in self.get_config()["regions"] if x["id"] == region_id), None)
        frame = self.state.frame
        info = resolve(key, self.state.usage) if key else None
        if not r or not r.get("portrait") or frame is None or not info or not info.get("species"):
            return False
        label = info["key"] if (is_mega(info["key"]) or not mega_forms(info["species"])) else info["species"]
        _, inner = crop_with_margin(frame, r["portrait"])
        self.portraits.add(inner, label)
        log.info("顔画像を記憶 (手動): %s", label)
        return True

    def _guess_mega(self, species, forms):
        if len(forms) == 1:
            return forms[0]
        entry = self.state.usage.find_by_species(species)
        megas = (entry or {}).get("mega") or []
        return max(megas, key=lambda m: m["pct"])["key"] if megas else forms[0]


def _ema(prev, value, k=0.2):
    return round(value if prev is None else prev * (1 - k) + value * k, 1)


def _lerp(a, b, i, n):
    t = i / (n - 1) if n > 1 else 0
    return {k: a[k] + (b[k] - a[k]) * t for k in ("x", "y", "w", "h")}
