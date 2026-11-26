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


class TestDedupe(unittest.TestCase):
    def _face(self, dx=0, color=(220, 60, 60)):
        from PIL import ImageDraw
        img = Image.new("RGB", (64, 64), (200, 40, 120))
        d = ImageDraw.Draw(img)
        d.ellipse((14 + dx, 10, 46 + dx, 50), fill=color)
        d.rectangle((26 + dx, 30, 36 + dx, 60), fill=(30, 30, 30))
        return img

    def test_near_duplicates_and_cap_are_removed(self):
        import tempfile
        from pathlib import Path
        from overlay.icons import IconDB
        db = IconDB(Path(tempfile.mkdtemp()))
        db.add(self._face(), "A")
        db.add(self._face(dx=1), "A")             # 1px ずれただけ → 重複
        db.add(self._face(color=(60, 60, 220)), "B")
        db.add(self._face(dx=-14, color=(250, 250, 90)), "A")  # 別の見た目 → 残す
        db.add(self._face(dx=12, color=(90, 250, 90)), "A")    # 3 枚目 → 上限 2 を超える
        self.assertEqual(db.dedupe(lambda k: k, 2), 2)
        self.assertEqual(sorted(it["key"] for it in db.list()), ["A", "A", "B"])
        self.assertIsNotNone(db.find_duplicate(self._face(dx=1), [it["id"] for it in db.list()]))
