"""画面を定期的に取得し、各領域の OCR → ポケモン名特定 → 状態更新を行う。"""
import io
import logging
import os
import threading
import time

from .capture import create_capture
from .config import save_config
from .icons import ICON_DIR, IconDB, crop_with_margin
from .gender import crop as crop_rect
from .gender import detect_gender
from .names import (NameMatcher, base_stats, form_abilities_ja, gender_variant, is_mega, mega_forms, normalize,
                    resolve)
from .ocr import create_ocr, preprocess

log = logging.getLogger(__name__)


class State:
    """オーバーレイに配信する状態。スレッド間で共有する。"""

    PARTY_SIZE = 6

    def __init__(self, usage_db, my_party=None, icons=None):
        self.usage = usage_db
        self.icons = icons     # IconDB (選出画面のアイコン照合)
        self.icon_crops = {}   # 未登録アイコンの切り出し画像 uid -> PIL.Image
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
                if side == "opp" and all(self._resolve(m)["species"] != info["species"] for m in party):
                    unknown = [m for m in party if m.startswith("icon:")]
                    if len(party) >= self.PARTY_SIZE and len(unknown) == 1:
                        # 選出画面の未登録アイコンが残り 1 つ → それがこのポケモンだと分かるので自動で覚える
                        self._label_icon_locked(unknown[0][5:], info["key"])
                        log.info("アイコンを自動登録: %s", info["display"])
                    elif len(party) < self.PARTY_SIZE:
                        party.append(info["key"])
            self.version += 1

    def _label_icon_locked(self, uid, key):
        crop = self.icon_crops.get(uid)
        if crop is not None and self.icons is not None:
            self.icons.add(crop, key)
        self.parties["opp"] = [key if m == "icon:" + uid else m for m in self.parties["opp"]]

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
            for side, members in self.parties.items():
                active = {s["species"] for s in self.slots.values() if s["side"] == side}
                parties[side] = [dict(self._resolve(m), active=self._resolve(m)["species"] in active)
                                 for m in members]
            return {"version": self.version, "slots": slots, "parties": parties,
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
            self.state.debug["preview"] = {"text": text}
            if any(k in normalize(text) or k in text for k in pv.get("keywords", ["選出"])):
                self.process_preview(frame, cfg, pv, scale)
                return
        self.process_battle(frame, cfg, scale)

    def process_preview(self, frame, cfg, pv, scale):
        n = int(pv.get("count", 6))
        now = time.time()
        if now - self.last_preview_seen > 30:  # しばらくぶりの選出画面 = 新しい対戦
            log.info("選出画面を検出: 新しい対戦")
            self.state.reset()
            self.pending.clear()
            self.battle_no += 1
            self.preview_opp, self.preview_my = [None] * n, [None] * n
        self.last_preview_seen = now
        self.state.status["screen"] = "選出画面"

        # 相手: アイコン照合
        thr = float(pv.get("icon_threshold", 0.8))
        for i in range(n):
            outer, inner = crop_with_margin(frame, _lerp(pv["opp_first"], pv["opp_last"], i, n))
            uid = f"b{self.battle_no}_{i}"
            self.state.icon_crops[uid] = inner
            cur = self.preview_opp[i]
            if cur and not cur.startswith("icon:"):
                continue  # 特定済み
            key, score = self.state.icons.match(outer) if self.state.icons else (None, 0.0)
            self.state.debug[f"icon{i}"] = {"match": key, "score": round(score, 3)}
            if key and score >= thr and pv.get("opp_gender_first"):
                g = detect_gender(crop_rect(frame, _lerp(pv["opp_gender_first"], pv["opp_gender_last"], i, n)))
                key = gender_variant(resolve(key, self.state.usage)["species"], g, self.state.usage) or key
            self.preview_opp[i] = key if key and score >= thr else "icon:" + uid
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
                if self.preview_my[i]:
                    continue
                texts = self._ocr_rect(frame, _lerp(pv["my_first"], pv["my_last"], i, n), scale)
                sp, _ = self.matcher.match(" ".join(texts)) if texts else (None, 0)
                if sp and pv.get("my_gender_first"):
                    g = detect_gender(crop_rect(frame, _lerp(pv["my_gender_first"], pv["my_gender_last"], i, n)))
                    sp = gender_variant(sp, g, self.state.usage) or sp
                self.preview_my[i] = sp
            mine = [m for m in self.preview_my if m]
            if len(mine) >= n - 2 and mine != self.state.parties["me"]:
                log.info("自分のパーティを登録: %s", "、".join(mine))
                self.state.set_party("me", mine)
                cfg["my_party"] = mine
                save_config(cfg)

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
            self.state.debug[r["id"]] = {"text": text, "match": species, "score": round(score, 2)}
            if not species:
                self.pending.pop(r["id"], None)
                continue
            prev, count = self.pending.get(r["id"], (None, 0))
            count = count + 1 if prev == species else 1
            self.pending[r["id"]] = (species, count)
            current = self.state.slots.get(r["id"])
            if count >= need:
                # 性別でデータが違うポケモン (イエッサン等) は、名前の横の ♀/♂ でどちらか決める
                gender = detect_gender(crop_rect(frame, r["gender"])) if r.get("gender") else None
                gkey = gender_variant(species, gender, self.state.usage)
                ident = gkey or species
                changed = current is None or current["species"] != species
                regender = (not changed and gkey and current["ident"] != gkey and not is_mega(current["ident"]))
                if changed or regender:
                    log.info("%s: %s%s (%.2f) ← %r", r["id"], species, f" [{gender}]" if gkey else "", score, text)
                    self.state.set_slot(r["id"], ident=ident, side=r.get("side", "opp"), source="auto",
                                        specific=bool(gkey))
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

    def learn_portrait(self, region_id, key):
        """手動でフォルムを選んだとき、そのときの顔画像をそのフォルムの見た目として覚える。"""
        r = next((x for x in self.get_config()["regions"] if x["id"] == region_id), None)
        frame = self.state.frame
        info = resolve(key, self.state.usage) if key else None
        if not r or not r.get("portrait") or frame is None or not info or not mega_forms(info["species"]):
            return False
        label = info["key"] if is_mega(info["key"]) else info["species"]
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
