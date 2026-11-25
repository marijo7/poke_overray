import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from overlay.icons import IconDB
from overlay.recognizer import Recognizer, State
from overlay.usage import UsageDB

MARKER = {"x": 0.3, "y": 0.2, "w": 0.4, "h": 0.1}


def screen(preview=True):
    """選出画面風 (マーカーの場所に白い帯と模様) / 対戦画面風"""
    im = Image.new("RGB", (400, 300), (30, 60, 120))
    d = ImageDraw.Draw(im)
    if preview:
        for i in range(8):
            d.rectangle((125 + i * 18, 64, 135 + i * 18, 86), fill="white")
        d.line((120, 75, 280, 75), fill=(240, 240, 240), width=2)
    else:
        d.ellipse((100, 40, 300, 120), fill=(200, 90, 40))
    return im


class NoOcr:  # 文字認識が全く読めない場合
    name = "none"
    default_scale = 1.0

    def recognize(self, img):
        return []


class TestMarker(unittest.TestCase):
    def setUp(self):
        self.cfg = {"layout": "switch", "ocr": {}, "preview": {"enabled": True, "marker": MARKER, "keywords": ["選出"]}}
        self.st = State(UsageDB(Path(tempfile.mkdtemp()) / "none.json"))
        self.rec = Recognizer(self.st, lambda: self.cfg)
        self.rec.ocr = NoOcr()
        self.rec.marker_db = IconDB(Path(tempfile.mkdtemp()))

    def test_learned_marker_works_without_ocr(self):
        pv = self.cfg["preview"]
        self.assertFalse(self.rec.detect_marker(screen(), self.cfg, pv, 1.0)[0])   # 覚える前: 判定できない
        self.st.frame = screen()
        self.assertTrue(self.rec.learn_marker())                                    # 「今の画面を選出画面として覚える」
        self.assertEqual(self.rec.detect_marker(screen(), self.cfg, pv, 1.0)[:3:2], (True, "image"))
        self.assertFalse(self.rec.detect_marker(screen(False), self.cfg, pv, 1.0)[0])  # 対戦画面は選出画面にならない

    def test_text_rules(self):
        ok = Recognizer.marker_text_ok
        self.assertTrue(ok("4匹 選出してください", ["選出"]))
        self.assertTrue(ok("4四：遅出してくだきい", ["選出"]) or ok("4匹－選出してくたさい", ["選出"]))
        self.assertTrue(ok("只下SSしてください", ["選出"]))      # 「してください」が読めれば良し
        self.assertFalse(ok("サイコフィールド状態", ["選出"]))
        self.assertFalse(ok("", ["選出"]))

    def test_white_text(self):
        img = Image.new("RGB", (20, 10), (150, 20, 60))
        img.putpixel((5, 5), (250, 250, 250))
        out = Recognizer.white_text(img)
        self.assertEqual(out.getpixel((5, 5)), (0, 0, 0))      # 白い文字 → 黒
        self.assertEqual(out.getpixel((0, 0)), (255, 255, 255))  # 背景 → 白


if __name__ == "__main__":
    unittest.main()
