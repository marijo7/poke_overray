import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from overlay.gender import detect_gender
from overlay.names import gender_variant
from overlay.recognizer import State
from overlay.usage import UsageDB

PINK, PURPLE = (188, 62, 113), (96, 92, 204)  # 実測した名前欄の色 (相手 / 自分)


def mark(bg, circle):
    im = Image.new("RGB", (40, 40), bg)
    ImageDraw.Draw(im).ellipse((4, 4, 36, 36), fill=circle)
    ImageDraw.Draw(im).line((20, 10, 20, 30), fill="white", width=3)
    return im


def usage():
    f = Path(tempfile.mkdtemp()) / "usage.json"
    base = {"moves": [], "items": [], "abilities": []}
    f.write_text(json.dumps({"pokemon": [
        {"key": "Indeedee-F", "display": "イエッサン(♀)", "species": "イエッサン", "rank": 8, **base},
        {"key": "Indeedee", "display": "イエッサン", "species": "イエッサン", "rank": 28, **base},
        {"key": "Garchomp", "display": "ガブリアス", "species": "ガブリアス", "rank": 3, **base},
    ]}, ensure_ascii=False), encoding="utf-8")
    return UsageDB(f)


class TestGender(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect_gender(mark(PINK, (210, 20, 30))), "F")     # 相手側の ♀
        self.assertEqual(detect_gender(mark(PURPLE, (215, 21, 31))), "F")   # 自分側の ♀
        self.assertEqual(detect_gender(mark(PURPLE, (26, 68, 215))), "M")   # 自分側の ♂
        self.assertEqual(detect_gender(mark(PINK, (26, 68, 215))), "M")     # 相手側の ♂
        self.assertIsNone(detect_gender(Image.new("RGB", (40, 40), PINK)))   # マーク無し (性別不明)
        self.assertIsNone(detect_gender(Image.new("RGB", (40, 40), PURPLE)))

    def test_variant(self):
        u = usage()
        self.assertEqual(gender_variant("イエッサン", "F", u), "Indeedee-F")
        self.assertEqual(gender_variant("イエッサン", "M", u), "Indeedee")
        self.assertIsNone(gender_variant("ガブリアス", "M", u))  # 性別でデータが分かれていない
        self.assertIsNone(gender_variant("イエッサン", None, u))

    def test_party_replaced_with_gendered_entry(self):
        st = State(usage())
        st.set_party("opp", ["イエッサン", "ガブリアス"])
        st.set_slot("opp2", ident="Indeedee-F", side="opp", specific=True)
        snap = st.snapshot([{"id": "opp2", "side": "opp"}])
        self.assertEqual(st.parties["opp"], ["Indeedee-F", "ガブリアス"])
        self.assertEqual(snap["slots"][0]["pokemon"]["key"], "Indeedee-F")
        self.assertEqual(snap["slots"][0]["stats"], [70, 55, 65, 95, 105, 85])  # メスの種族値


if __name__ == "__main__":
    unittest.main()
