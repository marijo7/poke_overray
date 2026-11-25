"""Pokémon Champions のアイコン画像 (Pokémon Champions Battle Data の公開画像) と、選出画面のアイコンを照合する。

選出画面のアイコンはゲームの 3D モデルの画像で、データ元の画像と同じ絵 (ポーズも同じ)。
ただし相手のアイコンは半透明のパネルの上にあって背景が透けるので、画像の「ポケモンの部分」(透明でない画素)
だけで比べる (背景は無視)。大きさ・位置を少しずつずらしながら探す。

- 色の比較: 通常の色のポケモンには強い (Switch 版の画面で 6/6、正解 0.85〜0.94 / 2 位は 0.67 以下)
- 輪郭の比較: 色違い (色が大きく違う) にも強い。タイプで候補を絞ったうえで色と組み合わせる
"""
import json
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)
ICON_DIR = Path(__file__).resolve().parent.parent / "data" / "champions_icons"
H = 48         # ゲームのアイコンをこの高さに縮小して比べる (タイプで絞った候補を色+輪郭で比べるとき)
H_ALL = 40     # 全ポケモンと色だけで比べるとき (実測でこちらの方が安定)
SCALES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)  # ポケモンの高さ (アイコン枠の高さに対する割合) の候補


def _color(img):
    return np.asarray(img.convert("RGB"), dtype=np.float32)


def _edges(img):
    return np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)[..., None]


def _unit(v):
    v = v - v.mean(0)
    v = v.reshape(-1)
    return v / max(float(np.linalg.norm(v)), 1e-6)


def _best(G, h, w, mask, t, step=1):  # step=2 だと 1px のずれで一致度が 0.44〜0.73 と大きく揺れたので 1
    """G の中で、大きさ (h, w) の窓をずらしながら、mask 内の画素の正規化相関の最大値"""
    if w > G.shape[1] or h > G.shape[0]:
        return -1.0
    win = np.lib.stride_tricks.sliding_window_view(G, (h, w, G.shape[2]))[::step, ::step, 0]
    px = win[:, :, mask]
    px = px - px.mean(axis=2, keepdims=True)
    px = px.reshape(px.shape[0], px.shape[1], -1)
    px /= np.maximum(np.linalg.norm(px, axis=2, keepdims=True), 1e-6)
    return float((px @ t).max())


class ChampionIcons:
    def __init__(self, directory: Path = ICON_DIR):
        self.dir = directory
        self.map = {}
        if (directory / "map.json").exists():
            self.map = json.loads((directory / "map.json").read_text(encoding="utf-8"))
        self._templates = {}
        self._lock = threading.Lock()

    def path(self, key):
        p = self.dir / f"{key}.png"
        return p if p.exists() else None

    def available(self, keys=None):
        keys = keys if keys is not None else self.map.keys()
        return [k for k in keys if self.path(k)]

    def _prep(self, key, H=H):
        with self._lock:
            if (key, H) in self._templates:
                return self._templates[(key, H)]
        im = Image.open(self.path(key)).convert("RGBA")
        im = im.crop(im.getbbox())
        temps = []
        for fr in SCALES:
            h = round(H * fr)
            w = max(4, round(im.width * h / im.height))
            r = im.resize((w, h), Image.BILINEAR)
            m = np.asarray(r)[..., 3] > 128
            if m.sum() < 20:
                continue
            bg = Image.new("RGBA", r.size, (128, 20, 60, 255))
            bg.alpha_composite(r)
            m2 = np.asarray(r.filter(ImageFilter.MaxFilter(3)))[..., 3] > 40  # 外形の輪郭も入るよう少し広げる
            temps.append((h, w, m, _unit(_color(r)[m]), m2, _unit(_edges(bg)[m2])))
        with self._lock:
            self._templates[(key, H)] = temps
        return temps

    def rank(self, crop: Image.Image, keys, use_edges=True):
        """戻り値: [(キー, 色の一致度, 輪郭の一致度), ...] (色+輪郭の平均の高い順。use_edges=False なら色だけ)"""
        h_ = H if use_edges else H_ALL
        g = crop.resize((max(1, round(crop.width * h_ / crop.height)), h_), Image.BILINEAR)
        GC = _color(g)
        GE = _edges(g) if use_edges else None
        out = []
        for k in self.available(keys):
            temps = self._prep(k, h_)
            if not temps:
                continue
            c = max(_best(GC, h, w, m, tc) for h, w, m, tc, m2, te in temps)
            e = max(_best(GE, h, w, m2, te) for h, w, m, tc, m2, te in temps) if use_edges else c
            out.append((k, c, e))
        out.sort(key=lambda x: -((x[1] + x[2]) / 2))
        return out
