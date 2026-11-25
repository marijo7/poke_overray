import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from overlay.icons import IconDB, crop_with_margin
from overlay.recognizer import State
from overlay.usage import UsageDB

RECT = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}


def fake_icon(shape, color, shift=0):
    """赤いパネル上にポケモン風の図形を描いた 200x200 の画面"""
    im = Image.new("RGB", (200, 200), (150, 20, 50))
    d = ImageDraw.Draw(im)
    x, y = 60 + shift, 60 + shift
    if shape == "circle":
        d.ellipse((x, y, x + 70, y + 80), fill=color)
    elif shape == "tri":
        d.polygon([(x + 35, y), (x, y + 80), (x + 70, y + 80)], fill=color)
    else:
        d.rectangle((x + 10, y, x + 60, y + 80), fill=color)
        d.rectangle((x, y + 30, x + 70, y + 45), fill=(20, 20, 20))
    return im


class TestIcons(unittest.TestCase):
    def setUp(self):
        self.db = IconDB(Path(tempfile.mkdtemp()))
        for key, shape, color in (("A", "circle", (230, 200, 40)), ("B", "tri", (40, 60, 200)),
                                  ("C", "cross", (230, 230, 230))):
            self.db.add(crop_with_margin(fake_icon(shape, color), RECT)[1], key)

    def test_match_with_shift_and_scale(self):
        frame = fake_icon("tri", (40, 60, 200), shift=4).resize((150, 150))
        key, score = self.db.match(crop_with_margin(frame, RECT)[0])
        self.assertEqual(key, "B")
        self.assertGreater(score, 0.8)

    def test_unknown_scores_low(self):
        frame = fake_icon("circle", (40, 200, 60))  # 形は A と同じだが色が違う
        key, score = self.db.match(crop_with_margin(frame, RECT)[0])
        self.assertLess(score, 0.8)

    def test_persist_and_remove(self):
        db2 = IconDB(self.db.dir)
        self.assertEqual(len(db2.items), 3)
        self.assertTrue(db2.remove(db2.items[0]["id"]))
        self.assertEqual(len(IconDB(self.db.dir).items), 2)

    def test_auto_label_last_unknown(self):
        """選出画面の未登録アイコンが残り 1 つのとき、対戦中に読み取った名前で自動登録される"""
        st = State(UsageDB(Path(tempfile.mkdtemp()) / "none.json"), icons=self.db)
        st.icon_crops = {"b1_5": crop_with_margin(fake_icon("circle", (40, 200, 60)), RECT)[1]}
        st.set_party("opp", ["ガブリアス", "ゴリランダー", "ミミッキュ", "サーフゴー", "ガオガエン", "icon:b1_5"])
        st.set_slot("opp1", ident="ドドゲザン", side="opp")
        self.assertEqual(st.parties["opp"][5], "ドドゲザン")
        self.assertEqual(self.db.items[-1]["key"], "ドドゲザン")

    def test_no_auto_label_when_ambiguous(self):
        st = State(UsageDB(Path(tempfile.mkdtemp()) / "none.json"), icons=self.db)
        st.set_party("opp", ["ガブリアス", "ゴリランダー", "ミミッキュ", "サーフゴー", "icon:b1_4", "icon:b1_5"])
        st.set_slot("opp1", ident="ドドゲザン", side="opp")
        self.assertEqual(len(st.parties["opp"]), 6)
        self.assertEqual(len(self.db.items), 3)


if __name__ == "__main__":
    unittest.main()
