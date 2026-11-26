import unittest

import numpy as np
from PIL import Image, ImageDraw

from overlay.autofit import Mapper, _ncc_search, find_my_names, find_opp_panels, refine_gender


class TestMapper(unittest.TestCase):
    def test_scale_and_move(self):
        # 基準 (1000x500) の (100, 50) の目印が、今の画面 (2000x1000) の (300, 200) にあり 2 倍の大きさ
        m = Mapper((1000, 500), (2000, 1000), 2.0, (100, 50), (300, 200))
        r = m.rect({"x": 0.2, "y": 0.2, "w": 0.1, "h": 0.1})  # 基準 px: (200, 100) 幅 100 高さ 50
        self.assertEqual(r, {"x": round((300 + 2 * 100) / 2000, 4), "y": round((200 + 2 * 50) / 1000, 4),
                             "w": 0.1, "h": 0.1})


class TestOppPanels(unittest.TestCase):
    def test_finds_red_column(self):
        img = Image.new("RGB", (1600, 900), (20, 20, 40))
        d = ImageDraw.Draw(img)
        for i in range(6):  # 赤いパネル 6 段 (左端 1200, 幅 200, 1 段目の上端 100, 間隔 110, 高さ 95)
            d.rectangle((1200, 100 + 110 * i, 1399, 194 + 110 * i), fill=(120, 8, 45))
            d.ellipse((1280, 120 + 110 * i, 1330, 170 + 110 * i), fill=(240, 230, 220))  # ポケモンの絵
        p = find_opp_panels(img)
        self.assertEqual(p["rows"], 6)
        self.assertAlmostEqual(p["x0"] * 1600, 1200, delta=2)
        self.assertAlmostEqual(p["x1"] * 1600, 1400, delta=2)
        self.assertAlmostEqual(p["top0"] * 900, 100, delta=2)
        self.assertAlmostEqual(p["pitch"] * 900, 110, delta=1)


class TestMyNames(unittest.TestCase):
    def test_names_not_items(self):
        """名前の行と、少し右から始まる持ち物の行が交互にある → 左端の揃った名前の行だけ"""
        lines = []
        for i in range(6):
            y = 0.12 + 0.117 * i
            lines.append({"text": f"なまえ{i}", "x0": 0.148, "y0": y, "x1": 0.2, "y1": y + 0.035})
            lines.append({"text": f"もちもの{i}", "x0": 0.167, "y0": y + 0.045, "x1": 0.23, "y1": y + 0.075})
        lines.append({"text": "0/4", "x0": 0.168, "y0": 0.83, "x1": 0.19, "y1": 0.86})
        names = find_my_names(lines, 2.17)
        self.assertEqual([l["text"] for l in names], [f"なまえ{i}" for i in range(6)])


class TestNccSearch(unittest.TestCase):
    def test_finds_pattern_ignoring_masked_part(self):
        rng = np.random.default_rng(0)
        area = rng.uniform(0, 255, (60, 120, 3)).astype(np.float32)
        tpl = area[20:40, 50:90].copy()
        mask = np.ones(tpl.shape[:2], np.float32)
        mask[5:15, 10:30] = 0  # 毎回変わる所 (名前など)
        tpl[5:15, 10:30] = 128
        area2 = area.copy()
        area2[25:35, 60:80] = 0  # 見本と違う内容が入っていても、重み 0 の所は無視される
        c, x, y = _ncc_search(area2, tpl, mask)
        self.assertEqual((x, y), (50, 20))
        self.assertGreater(c, 0.99)


class TestRefineGender(unittest.TestCase):
    def test_moves_to_mark(self):
        img = Image.new("RGB", (1000, 500), (90, 80, 200))
        ImageDraw.Draw(img).ellipse((520, 100, 550, 130), fill=(230, 20, 30))  # ♀ の赤丸 (中心 535)
        g = refine_gender(img, {"x": 0.49, "y": 0.2, "w": 0.03, "h": 0.06})
        self.assertAlmostEqual((g["x"] + g["w"] / 2) * 1000, 535, delta=4)

    def test_keeps_when_no_mark(self):
        img = Image.new("RGB", (1000, 500), (90, 80, 200))
        g = {"x": 0.49, "y": 0.2, "w": 0.03, "h": 0.06}
        self.assertEqual(refine_gender(img, g), g)


if __name__ == "__main__":
    unittest.main()
