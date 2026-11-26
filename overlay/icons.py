"""選出画面の相手ポケモンのアイコンを、登録済みのアイコン画像と照合する。

ゲームのアイコンは毎回同じ絵なので、一度「このアイコンは〇〇」と登録すれば
次回からは画像の一致度 (正規化相互相関) で特定できる。
位置の微妙なずれに対応するため、少し広めに切り出して ±数px ずらしながら比較する。
"""
import json
import threading
import uuid
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ICON_DIR = Path(__file__).resolve().parent.parent / "data" / "icons"
SIZE = 32       # 比較用に縮小する大きさ
MARGIN = 0.1    # 切り出し範囲の外側に足す余白 (ずれの許容量)


def _feat(a: np.ndarray) -> np.ndarray:
    """(..., S, S, 3) → 正規化ベクトル。色チャンネルごとに平均を引くことで、
    パネルの赤い背景の影響を消してポケモンの形・模様で比較する (誤一致のスコアが 0.9 → 0.5 程度に下がる)。"""
    a = a - a.mean(axis=(-3, -2), keepdims=True)
    v = a.reshape(*a.shape[:-3], -1)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-6)


def template_feature(img: Image.Image) -> np.ndarray:
    return _feat(np.asarray(img.convert("RGB").resize((SIZE, SIZE), Image.BILINEAR), dtype=np.float32))


def window_features(img_with_margin: Image.Image) -> np.ndarray:
    """余白付きの切り出し画像から、少しずつずらした SIZE×SIZE の窓をすべて取り出す。"""
    pad = round(SIZE * MARGIN)
    full = SIZE + 2 * pad
    a = np.asarray(img_with_margin.convert("RGB").resize((full, full), Image.BILINEAR), dtype=np.float32)
    wins = np.lib.stride_tricks.sliding_window_view(a, (SIZE, SIZE, 3))[:, :, 0]  # (2p+1, 2p+1, S, S, 3)
    return _feat(wins.reshape(-1, SIZE, SIZE, 3))


def crop_with_margin(frame: Image.Image, rect: dict):
    """rect (割合座標) を切り出す。戻り値: (余白付き, 余白なし)"""
    W, H = frame.size
    x0, y0, x1, y1 = rect["x"] * W, rect["y"] * H, (rect["x"] + rect["w"]) * W, (rect["y"] + rect["h"]) * H
    mx, my = (x1 - x0) * MARGIN, (y1 - y0) * MARGIN
    outer = frame.crop((int(x0 - mx), int(y0 - my), int(x1 + mx), int(y1 + my)))
    inner = frame.crop((int(x0), int(y0), int(x1), int(y1)))
    return outer, inner


def dup_feature(img: Image.Image) -> np.ndarray:
    """ほとんど同じ画像かどうかを見る用の特徴 (16x16 にぼかして縮小。1〜2px のずれは同じとみなせる)。
    実測: 同じ顔を 1〜2px ずらしたもの 0.94 以上 / 別のポケモン 0.62 以下"""
    a = np.asarray(img.convert("RGB").filter(ImageFilter.GaussianBlur(1)).resize((16, 16), Image.BILINEAR),
                   dtype=np.float32)
    a = a - a.mean(axis=(0, 1))
    v = a.ravel()
    return v / max(float(np.linalg.norm(v)), 1e-6)


DUP_THRESHOLD = 0.85


class IconDB:
    def __init__(self, directory: Path = ICON_DIR):
        self.dir = directory
        self.lock = threading.Lock()
        self.items = []          # [{"id", "key"}]
        self.features = np.zeros((0, SIZE * SIZE * 3), np.float32)
        self._dup_cache = {}
        self.load()

    @property
    def _index(self):
        return self.dir / "index.json"

    def load(self):
        with self.lock:
            self.items, feats = [], []
            if self._index.exists():
                for it in json.loads(self._index.read_text(encoding="utf-8")):
                    p = self.dir / f"{it['id']}.png"
                    if p.exists():
                        self.items.append(it)
                        feats.append(template_feature(Image.open(p)))
            self.features = np.stack(feats) if feats else np.zeros((0, SIZE * SIZE * 3), np.float32)

    def _save_index(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    def add(self, img: Image.Image, key: str) -> str:
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            iid = uuid.uuid4().hex[:12]
            img.convert("RGB").save(self.dir / f"{iid}.png")
            self.items.append({"id": iid, "key": key})
            self.features = np.vstack([self.features, template_feature(img)[None]])
            self._save_index()
            return iid

    def remove(self, iid: str):
        with self.lock:
            idx = next((i for i, it in enumerate(self.items) if it["id"] == iid), None)
            if idx is None:
                return False
            self.items.pop(idx)
            self.features = np.delete(self.features, idx, axis=0)
            self._dup_cache.pop(iid, None)
            (self.dir / f"{iid}.png").unlink(missing_ok=True)
            self._save_index()
            return True

    def _dup(self, iid):
        if iid not in self._dup_cache:
            p = self.dir / f"{iid}.png"
            self._dup_cache[iid] = dup_feature(Image.open(p)) if p.exists() else None
        return self._dup_cache[iid]

    def find_duplicate(self, img: Image.Image, ids):
        """ids の中に img とほとんど同じ画像があればその id"""
        f = dup_feature(img)
        for iid in ids:
            g = self._dup(iid)
            if g is not None and float(f @ g) >= DUP_THRESHOLD:
                return iid
        return None

    def dedupe(self, group, max_per: int):
        """ほとんど同じ画像と、1 つのグループ (group(キー) が同じもの) に max_per 枚を超える分を消す (古いものを残す)。
        戻り値: 消した数"""
        kept, drop = {}, []
        for it in self.list():
            g = group(it["key"])
            ids = kept.setdefault(g, [])
            if len(ids) >= max_per or self.find_duplicate(Image.open(self.dir / f"{it['id']}.png"), ids):
                drop.append(it["id"])
            else:
                ids.append(it["id"])
        for iid in drop:
            self.remove(iid)
        return len(drop)

    def image_path(self, iid: str):
        p = self.dir / f"{iid}.png"
        return p if p.exists() else None

    def match(self, outer: Image.Image, keys=None):
        """戻り値: (key, スコア 0〜1)。keys を渡すとその中だけで照合。登録が無ければ (None, 0)。"""
        with self.lock:
            idx = [i for i, it in enumerate(self.items) if keys is None or it["key"] in keys]
            if not idx:
                return None, 0.0
            scores = window_features(outer) @ self.features[idx].T  # (窓数, 候補数)
            best = scores.max(axis=0)
            j = int(best.argmax())
            return self.items[idx[j]]["key"], float(best[j])

    def has_key(self, key) -> bool:
        with self.lock:
            return any(it["key"] == key for it in self.items)

    def list(self):
        with self.lock:
            return list(self.items)
