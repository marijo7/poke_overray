"""選出画面のタイプアイコンを、データ元 (Pokémon Champions Battle Data) のタイプアイコン 18 種と照合する。

データ元のアイコンは丸、ゲームの中は角の丸い四角で、記号の大きさも違うので、画像をそのまま重ねても合わない
(実測 27 枚中 11 枚しか当たらなかった)。そこで
  - 背景の色 (白い記号以外の、色の付いた部分の中央値)
  - 白い記号の形 (記号を囲む範囲を同じ大きさにそろえて比べる)
の 2 つで比べる (実測: 27 枚すべて正解、2 位との差 0.61 以上)。
画像は tools/update_usage.py が data/champions_icons/types/ に取得する。
"""
import threading
from pathlib import Path

import numpy as np
from PIL import Image

TYPE_DIR = Path(__file__).resolve().parent.parent / "data" / "champions_icons" / "types"
SHAPE = 24


def feature(img: Image.Image):
    """タイプアイコン → (背景色 RGB, 白い記号の形のベクトル)"""
    a = np.asarray(img.convert("RGBA"), dtype=np.float32)
    rgb, al = a[..., :3], a[..., 3] / 255
    white = (rgb.min(axis=2) > 190) & (al > 0.5)
    colored = (~white) & (al > 0.9) & (rgb.max(axis=2) - rgb.min(axis=2) > 40)
    bg = np.median(rgb[colored], axis=0) if colored.sum() > 10 else rgb.reshape(-1, 3).mean(axis=0)
    ys, xs = np.where(white)
    if len(xs) < 5:
        return bg, np.zeros(SHAPE * SHAPE, np.float32)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    s = max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    m = Image.fromarray((white * 255).astype(np.uint8)).crop(
        (int(cx - s / 2), int(cy - s / 2), int(cx + s / 2), int(cy + s / 2))).resize((SHAPE, SHAPE), Image.BILINEAR)
    v = np.asarray(m, np.float32).ravel()
    v = v - v.mean()
    return bg, v / max(float(np.linalg.norm(v)), 1e-6)


def similarity(f1, f2):
    (c1, v1), (c2, v2) = f1, f2
    return float(v1 @ v2) - 1.5 * float(np.linalg.norm(c1 - c2)) / 441.0


class TypeIcons:
    def __init__(self, directory: Path = TYPE_DIR):
        self.dir = directory
        self.lock = threading.Lock()
        self.refs = {}
        self.loaded_at = None

    def _load(self):
        if not self.dir.exists():
            return
        mtime = self.dir.stat().st_mtime
        if self.loaded_at == mtime:
            return
        refs = {}
        for p in self.dir.glob("*.png"):
            try:
                refs[p.stem.lower()] = feature(Image.open(p))
            except OSError:
                continue
        self.refs, self.loaded_at = refs, mtime

    def classify(self, img: Image.Image, min_margin: float = 0.3):
        """戻り値: (タイプ, 2 位との差) / はっきりしなければ (None, 差)"""
        with self.lock:
            self._load()
            if len(self.refs) < 2:
                return None, 0.0
            f = feature(img)
            sc = sorted(((similarity(f, g), k) for k, g in self.refs.items()), reverse=True)
        margin = sc[0][0] - sc[1][0]
        return (sc[0][1] if margin >= min_margin else None), margin
