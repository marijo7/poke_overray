import unittest

from overlay.names import NameMatcher, normalize, showdown_to_ja


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

    def test_reject_garbage(self):
        self.assertIsNone(self.m.match("HP 152/152")[0])
        self.assertIsNone(self.m.match("")[0])

    def test_showdown(self):
        self.assertEqual(showdown_to_ja("Charizard-Mega-Y"), ("メガリザードンY", "リザードン"))
        self.assertEqual(showdown_to_ja("Ninetales-Alola"), ("キュウコン(アローラ)", "キュウコン"))
        self.assertEqual(showdown_to_ja("Kommo-o"), ("ジャラランガ", "ジャラランガ"))
        self.assertEqual(showdown_to_ja("Mr. Rime")[1], "バリコオル")

    def test_normalize(self):
        self.assertEqual(normalize("ｶﾞﾌﾞﾘｱｽ"), "ガブリアス")


if __name__ == "__main__":
    unittest.main()
