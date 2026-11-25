import unittest

from overlay.names import NameMatcher, normalize, resolve, showdown_to_ja


class TestNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = NameMatcher()

    def test_exact(self):
        self.assertEqual(self.m.match("ガブリアス")[0], "ガブリアス")

    def test_ocr_noise(self):
        # Windows OCR は日本語の文字間に空白を入れる / 長音が漢字の「一」になる
        self.assertEqual(self.m.match("ガ ブ リ ア ス")[0], "ガブリアス")
        self.assertEqual(self.m.match("サ一フゴ一 Lv.50")[0], "サーフゴー")
        self.assertEqual(self.m.match("ゴリランダ ー♂")[0], "ゴリランダー")

    def test_italic_confusions(self):
        # スマホ版の対戦画面 (斜体) で実際に出た誤読
        self.assertEqual(self.m.match("グレソアルマ")[0], "グレンアルマ")
        self.assertEqual(self.m.match("イエツサン")[0], "イエッサン")
        self.assertEqual(self.m.match("ビピヨソ")[0], "ビビヨン")
        self.assertEqual(self.m.match("ピビヨン")[0], "ビビヨン")
        self.assertEqual(self.m.match("フラエッテーーア")[0], "フラエッテ")
        # 同一視しても本物の名前が優先される
        self.assertEqual(self.m.match("ソーナンス")[0], "ソーナンス")
        self.assertEqual(self.m.match("ピカチュウ")[0], "ピカチュウ")

    def test_hiragana_and_extra(self):
        self.assertEqual(self.m.match("あいての ドドゲザン")[0], "ドドゲザン")

    def test_fuzzy(self):
        self.assertEqual(self.m.match("ブリジュラヌ")[0], "ブリジュラス")

    def test_longest_wins(self):
        self.assertEqual(self.m.match("ミュウツー")[0], "ミュウツー")

    def test_digit_kept(self):
        self.assertEqual(normalize(self.m.match("ポリゴン2")[0]), "ポリゴン2")

    def test_mega(self):
        self.assertEqual(self.m.match("メガリザードン")[0], "リザードン")
        self.assertEqual(self.m.match("メガリザードンY")[0], "リザードン")
        self.assertEqual(self.m.match("メガニウム")[0], "メガニウム")
        self.assertEqual(self.m.match("メガヤンマ")[0], "メガヤンマ")

    def test_reject_garbage(self):
        self.assertIsNone(self.m.match("HP 152/152")[0])
        self.assertIsNone(self.m.match("")[0])

    def test_showdown(self):
        self.assertEqual(showdown_to_ja("Charizard-Mega-Y"), ("メガリザードンY", "リザードン"))
        self.assertEqual(showdown_to_ja("Ninetales-Alola"), ("キュウコン(アローラ)", "キュウコン"))
        self.assertEqual(showdown_to_ja("Kommo-o"), ("ジャラランガ", "ジャラランガ"))
        self.assertEqual(showdown_to_ja("Mr. Rime")[1], "バリコオル")

    def test_resolve_sprite(self):
        self.assertEqual(resolve("Charizard-Mega-Y")["sprite"], 10035)
        self.assertEqual(resolve("Indeedee-F")["sprite"], 10186)
        self.assertEqual(resolve("Basculegion")["sprite"], 902)
        self.assertEqual(resolve("メガガブリアスZ")["sprite"], 10309)
        self.assertEqual(resolve("キュウコン(アローラ)")["key"], "Ninetales-Alola")
        self.assertEqual(resolve("ミミッキュ")["sprite"], 778)

    def test_resolve_with_usage_variants(self):
        """表記ゆれ (空白区切り・カッコ・種族名だけ) でも採用率データの同じフォルムになる"""
        import tempfile, json
        from pathlib import Path
        from overlay.usage import UsageDB
        f = Path(tempfile.mkdtemp()) / "usage.json"
        f.write_text(json.dumps({"pokemon": [{"key": "Floette-Eternal", "display": "フラエッテ(えいえんのはな)",
                                              "species": "フラエッテ", "rank": 1, "moves": [], "items": [],
                                              "abilities": []}]}, ensure_ascii=False), encoding="utf-8")
        from overlay.recognizer import State
        st = State(UsageDB(f))
        for s in ("フラエッテ えいえんのはな", "フラエッテ(えいえんのはな)", "フラエッテ", "floette-eternal"):
            r = st._resolve(s)
            self.assertEqual((r["key"], r["has_data"], r["sprite"]), ("Floette-Eternal", True, 10061), s)

    def test_normalize(self):
        self.assertEqual(normalize("ｶﾞﾌﾞﾘｱｽ"), "ガブリアス")


if __name__ == "__main__":
    unittest.main()


class TestStatsNature(unittest.TestCase):
    def test_base_stats(self):
        from overlay.names import base_stats, resolve
        self.assertEqual(base_stats(resolve("ガブリアス")["sprite"]), [108, 130, 95, 80, 85, 102])
        self.assertEqual(sum(base_stats(resolve("メガフラエッテ")["sprite"])), 651)  # メガシンカ後の種族値
        self.assertIsNone(base_stats(None))

    def test_nature_and_spread(self):
        from overlay.names import format_spread, nature_info
        self.assertEqual(nature_info("Jolly"), {"name": "ようき", "up": "S", "down": "C"})
        self.assertEqual(nature_info("Hardy")["up"], None)
        self.assertEqual(format_spread("2/32/0/0/0/32"), "H2 A32 S32")

    def test_parse_top_build(self):
        import sys
        sys.path.insert(0, "tools")
        from update_usage import parse_top_build
        md = ("The top build for Garchomp features a **Jolly** nature with an EV spread of `2/32/0/0/0/32`. "
              "This configuration accounts for 33.040% of competitive builds.")
        b = parse_top_build(md)
        self.assertEqual((b["nature"]["name"], b["spread_text"], b["pct"]), ("ようき", "H2 A32 S32", 33.04))
        hidden = parse_top_build(md.replace("**Jolly**", "****"))  # 実データの伏せ字の形
        self.assertIsNone(hidden["nature"])  # 性格が伏せられているフォーマット
        self.assertIsNone(parse_top_build("No EV spread or nature data available."))
