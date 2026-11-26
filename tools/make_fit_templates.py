"""開発用: 範囲の自動調整に使うパネルの見本 (data/fit/*.npz) を、基準のスクリーンショットから作る。

    python tools/make_fit_templates.py phone スマホ版の対戦画面.png
    python tools/make_fit_templates.py switch Switch版の対戦画面.png

スクリーンショットは、プリセット (overlay/config.py) の対戦画面の範囲を測ったものと同じ画面にすること。
見本には名前・顔画像・HP・性別マークの部分は含まれない (灰色で塗りつぶす)。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from overlay.autofit import save_templates  # noqa: E402
from overlay.config import PRESETS  # noqa: E402

if __name__ == "__main__":
    layout, path = sys.argv[1], sys.argv[2]
    save_templates(layout, Image.open(path), PRESETS[layout]["regions"])
    print("保存しました:", layout)
