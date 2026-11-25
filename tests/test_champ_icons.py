import json
import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from overlay.champ_icons import ChampionIcons


def sprite(kind):
    """透明な背景にポケモン風の図形 (データ元の画像の代わり)"""
    im = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if kind == "A":
        d.ellipse((20, 10, 108, 118), fill=(240, 200, 40, 255)); d.rectangle((50, 40, 78, 70), fill=(30, 30, 30, 255))
    elif kind == "B":
        d.polygon([(64, 4), (8, 124), (120, 124)], fill=(60, 80, 220, 255)); d.ellipse((50, 60, 78, 88), fill="white")
    else:
        d.rectangle((40, 4, 88, 124), fill=(40, 180, 70, 255)); d.rectangle((10, 50, 118, 70), fill=(200, 40, 40, 255))
    return im


def game_icon(kind, seed=0):
    """選出画面のアイコン風: 透けた背景 (線が入る) の上に、少し小さく・ずらして置く"""
    rnd = random.Random(seed)
    bg = Image.new("RGBA", (150, 110), (150, 20, 60, 255))
    d = ImageDraw.Draw(bg)
    for _ in range(6):  # 背景の模様 (照明の線)
        y = rnd.randint(0, 110)
        d.line((0, y, 150, y + rnd.randint(-40, 40)), fill=(200, 90, 150, 255), width=2)
    s = sprite(kind).resize((82, 82))
    bg.alpha_composite(s, (30 + rnd.randint(-6, 6), 14 + rnd.randint(-4, 4)))
    return bg.convert("RGB")


class TestChampionIcons(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        for k in "ABC":
            sprite(k).save(d / f"{k}.png")
        (d / "map.json").write_text(json.dumps({k: f"{k}.png" for k in "ABC"}))
        self.ci = ChampionIcons(d)

    def test_rank_ignores_background(self):
        for k in "ABC":
            for seed in range(3):
                top = self.ci.rank(game_icon(k, seed), list("ABC"))[0][0]
                self.assertEqual(top, k, (k, seed))

    def test_color_only_mode(self):
        r = self.ci.rank(game_icon("B", 1), list("ABC"), use_edges=False)
        self.assertEqual(r[0][0], "B")
        self.assertGreater(r[0][1] - r[1][1], 0.1)

    def test_missing_images_are_skipped(self):
        self.assertEqual([k for k, *_ in self.ci.rank(game_icon("A"), ["A", "Z"])], ["A"])


if __name__ == "__main__":
    unittest.main()
