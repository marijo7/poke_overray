"""名前の横の性別マーク (赤丸に♀ / 青丸に♂) を色で判定する。

イエッサン・ニャオニクス・イダイトウ等は、オスとメスで種族値・特性・技が違うため、採用率データも別になっている。
"""
import numpy as np
from PIL import Image


def detect_gender(img: Image.Image, min_ratio: float = 0.15):
    """戻り値: "F" (♀) / "M" (♂) / None (性別なし・判定できない)

    ♀ の丸は純粋な赤 (R 高・G,B 低)、♂ の丸は青 (B 高・R 低)。
    相手側の名前欄はピンク (B がそこそこ高い)、自分側は青紫 (R がそこそこ高い) なので、それぞれで区別する。
    """
    a = np.asarray(img.convert("RGB"), dtype=np.int16)
    if a.size == 0:
        return None
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    # 実測 (スマホ版): ♀の丸 B≈20-30 / ピンクの名前欄 B≈110、♂の丸 R≈10-30 / 青紫の名前欄 R≈95
    red = (r > 160) & (g < 60) & (b < 60)
    blue = (b > 170) & (r < 55)
    n = r.size
    fr, fb = red.sum() / n, blue.sum() / n
    if max(fr, fb) < min_ratio:
        return None
    return "F" if fr > fb else "M"


def crop(frame: Image.Image, rect: dict) -> Image.Image:
    W, H = frame.size
    return frame.crop((int(rect["x"] * W), int(rect["y"] * H),
                       int((rect["x"] + rect["w"]) * W), int((rect["y"] + rect["h"]) * H)))
