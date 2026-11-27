import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from overlay.icons import IconDB
from overlay.names import types_of
from overlay.recognizer import Recognizer, State
from overlay.usage import UsageDB

COLORS = {"fighting": (240, 120, 20), "poison": (140, 60, 190), "dragon": (60, 90, 220), "ground": (150, 90, 40),
          "fire": (230, 40, 40), "flying": (130, 180, 240)}


def type_icon(t, empty=False):
    """色と模様がタイプごとに違う、タイプアイコン風の画像 (余白付き / 余白なし)"""
    inner = Image.new("RGB", (40, 40), (150, 20, 60))
    if not empty:
        d = ImageDraw.Draw(inner)
        d.rounded_rectangle((2, 2, 38, 38), 6, fill=COLORS[t])
        k = list(COLORS).index(t)
        d.polygon([(8 + k * 3, 30), (20, 6 + k * 2), (32 - k, 30)], fill="white")
        d.ellipse((14, 14 + k, 26, 26 + k), outline="white", width=3)
    outer = Image.new("RGB", (48, 48), (150, 20, 60))
    outer.paste(inner, (4, 4))
    return outer, inner


def usage():
    f = Path(tempfile.mkdtemp()) / "usage.json"
    base = {"moves": [], "items": [], "abilities": []}
    f.write_text(json.dumps({"meta": {"roster": ["Sneasler", "Garchomp", "Charizard", "Talonflame", "Rillaboom"]},
                             "pokemon": [{"key": "Garchomp", "display": "ガブリアス", "species": "ガブリアス",
                                          "rank": 3, **base}]}, ensure_ascii=False), encoding="utf-8")
    return UsageDB(f)


class TestTypes(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.state = State(usage(), icons=IconDB(d / "icons"))
        self.state.type_db = IconDB(d / "types")
        self.rec = Recognizer(self.state, lambda: {"preview": {}})

    def test_learn_and_guess(self):
        st = self.state
        self.assertEqual(types_of("Sneasler"), ["fighting", "poison"])
        # 1 回目: 名前の分かったアイコンからタイプを覚える
        st.type_crops = {"a": (type_icon("fighting"), type_icon("poison")),
                         "b": (type_icon("dragon"), type_icon("ground")),
                         "c": (type_icon("fire"), type_icon("flying"))}
        for uid, key in (("a", "Sneasler"), ("b", "Garchomp"), ("c", "Charizard")):
            st.learn_types(uid, key)
        self.assertEqual(len(st.type_db.items), 6)
        # 2 回目: タイプを読んで候補を出す
        st.type_crops = {"x": (type_icon("dragon"), type_icon("ground")),
                         "y": (type_icon("fire"), type_icon("flying"))}
        self.rec.preview_opp = ["icon:x", "icon:y"]
        self.assertEqual(self.rec.read_types("x"), ["dragon", "ground"])
        self.assertEqual(self.rec.guess_by_types(None, {}, 0, 2, "x"), "Garchomp")    # 候補 1 匹 → 推定
        self.assertIsNone(self.rec.guess_by_types(None, {}, 1, 2, "y"))                 # 候補 2 匹 → 候補だけ
        self.assertEqual(st.icon_guess["y"]["cands"], ["Charizard", "Talonflame"])

    def test_single_type_is_on_the_right(self):
        st = self.state
        st.type_crops = {"a": (type_icon("fire"), type_icon("flying"))}
        st.learn_types("a", "Charizard")
        st.type_crops["g"] = (type_icon("fire", empty=True), type_icon("fire"))  # 1 タイプ: 左は空
        self.assertEqual(self.rec.read_types("g"), ["fire"])

    def test_unknown_type_returns_none(self):
        st = self.state
        st.type_crops = {"a": (type_icon("fire"), type_icon("flying"))}
        st.learn_types("a", "Charizard")
        st.type_crops["z"] = (type_icon("dragon"), type_icon("ground"))  # まだ覚えていないタイプ
        self.assertIsNone(self.rec.read_types("z"))

    def test_guess_confirmed_when_it_appears(self):
        st = self.state
        st.icon_crops["x"] = type_icon("dragon")[1]
        st.type_crops = {"x": (type_icon("dragon"), type_icon("ground"))}
        st.icon_guess["x"] = {"types": ["dragon", "ground"], "cands": ["Garchomp"], "guess": "Garchomp"}
        st.set_party("opp", ["Garchomp"])
        st.set_slot("opp1", ident="ガブリアス", side="opp")
        self.assertEqual([it["key"] for it in st.icons.list()], ["Garchomp"])  # 出てきたのでアイコンとして覚えた
        self.assertNotIn("x", st.icon_guess)


if __name__ == "__main__":
    unittest.main()


class TestPartialTypes(unittest.TestCase):
    def test_types_match(self):
        from overlay.recognizer import types_match
        self.assertTrue(types_match(["water", "ghost"], ["water", "ghost"]))
        self.assertTrue(types_match(["water", "ghost"], [None, "ghost"]))   # 左が読めなかった
        self.assertFalse(types_match(["ghost", "water"], [None, "ghost"]))  # 並び (左右) が違う
        self.assertFalse(types_match(["ghost"], [None, "ghost"]))           # タイプの数が違う
        self.assertFalse(types_match(["water", "ghost"], None))

    def test_water_ghost_is_only_basculegion(self):
        """みず/ゴーストは Champions ではイダイトウ (オス・メス) だけ → タイプが読めれば 1 匹に決まる"""
        from overlay.names import resolve, types_of
        from overlay.recognizer import types_match
        from overlay.usage import UsageDB
        u = UsageDB()
        if not u.roster():
            self.skipTest("採用率データ (data/usage.json) が無い")
        hits = {resolve(k, u)["species"] for k in u.roster() if types_match(types_of(k, u), ["water", "ghost"])}
        self.assertEqual(hits, {"イダイトウ"})


class TestBuiltinTypeIcons(unittest.TestCase):
    def test_builtin_types_present(self):
        from overlay.icons import ICON_DIR, IconDB
        db = IconDB(ICON_DIR.parent / "types_builtin")
        keys = {it["key"] for it in db.list()}
        self.assertTrue({"water", "ghost", "fire", "dragon", "fairy"} <= keys)
        self.assertEqual(len(keys), 17)  # むし以外


class TestTypeIconsFromSource(unittest.TestCase):
    def test_round_icon_matches_square_game_icon(self):
        """データ元の丸いアイコンと、ゲームの角の丸い四角いアイコン (記号の大きさも違う) を、色と記号の形で見分ける"""
        from overlay.type_icons import TypeIcons

        def icon(color, shape, round_icon, size):
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0) if round_icon else (*color, 255))
            d = ImageDraw.Draw(img)
            if round_icon:
                d.ellipse((0, 0, size - 1, size - 1), fill=(*color, 255))
            m = size * (0.3 if round_icon else 0.15)  # ゲームの方が記号が大きい
            box = (m, m, size - m, size - m)
            if shape == "drop":
                d.polygon([(size / 2, box[1]), (box[2], box[3]), (box[0], box[3])], fill=(255, 255, 255, 255))
            else:
                d.rectangle(box, fill=(255, 255, 255, 255))
            return img

        d = Path(tempfile.mkdtemp())
        icon((40, 130, 240), "drop", True, 64).save(d / "water.png")
        icon((40, 130, 240), "box", True, 64).save(d / "blockwater.png")
        icon((240, 60, 40), "drop", True, 64).save(d / "fire.png")
        t = TypeIcons(d)
        self.assertEqual(t.classify(icon((45, 125, 235), "drop", False, 40))[0], "water")
        self.assertEqual(t.classify(icon((235, 65, 45), "drop", False, 40))[0], "fire")
