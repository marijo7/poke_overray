"""画面を定期的に取得し、各領域の OCR → ポケモン名特定 → 状態更新を行う。"""
import io
import logging
import os
import threading
import time

from .capture import create_capture
from .names import NameMatcher
from .ocr import create_ocr, preprocess

log = logging.getLogger(__name__)


class State:
    """オーバーレイに配信する状態。スレッド間で共有する。"""

    def __init__(self, usage_db):
        self.usage = usage_db
        self.lock = threading.Lock()
        self.slots = {}        # region id -> {"key", "species", "source", "updated"}
        self.history = []      # この対戦で見た相手 (usage key または種族名)
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

    def set_slot(self, region_id, species=None, key=None, source="auto"):
        with self.lock:
            entry = self.usage.get(key) if key else (self.usage.find_by_species(species) if species else None)
            if species is None and key is None:
                self.slots.pop(region_id, None)
            else:
                self.slots[region_id] = {
                    "key": entry["key"] if entry else None,
                    "species": entry["species"] if entry else species,
                    "source": source, "updated": time.time(),
                }
                ident = entry["key"] if entry else species
                if ident not in self.history:
                    self.history.append(ident)
            self.version += 1

    def reset(self):
        with self.lock:
            self.slots.clear()
            self.history.clear()
            self.version += 1

    def snapshot(self, regions):
        with self.lock:
            slots = []
            for r in regions:
                s = self.slots.get(r["id"])
                entry = self.usage.get(s["key"]) if s and s["key"] else None
                slots.append({
                    "id": r["id"], "label": r.get("label", r["id"]),
                    "species": s["species"] if s else None,
                    "source": s["source"] if s else None,
                    "pokemon": entry,
                    "debug": self.debug.get(r["id"]),
                })
            history = []
            for ident in self.history:
                e = self.usage.get(ident)
                history.append({"key": ident, "display": e["display"] if e else ident, "has_data": bool(e)})
            return {"version": self.version, "slots": slots, "history": history,
                    "status": dict(self.status), "perf": dict(self.perf), "usage_meta": self.usage.meta}


class Recognizer(threading.Thread):
    def __init__(self, state: State, get_config):
        super().__init__(daemon=True)
        self.state = state
        self.get_config = get_config
        self.stop_event = threading.Event()
        self.reload_event = threading.Event()
        self.pending = {}  # region id -> (species, 連続回数)

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

    def process_frame(self, frame, cfg):
        W, H = frame.size
        need = max(1, int(cfg.get("confirm_frames", 2)))
        scale = cfg["ocr"].get("scale", getattr(self.ocr, "default_scale", 2.0))
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
            if count >= need and (current is None or current["species"] != species):
                log.info("%s: %s (%.2f) ← %r", r["id"], species, score, text)
                self.state.set_slot(r["id"], species=species, source="auto")


def _ema(prev, value, k=0.2):
    return round(value if prev is None else prev * (1 - k) + value * k, 1)
