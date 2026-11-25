import json
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from overlay.icons import IconDB
from overlay.names import mega_form_for_stone, mega_forms
from overlay.recognizer import Recognizer, State
from overlay.usage import UsageDB
from tests.test_icons import fake_icon

PORTRAIT = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
REGION = {"id": "opp1", "side": "opp", "x": 0, "y": 0, "w": 0.1, "h": 0.1, "portrait": PORTRAIT}
NORMAL = fake_icon("circle", (230, 200, 40))
MEGA = fake_icon("tri", (40, 60, 200))


def usage_file():
    f = Path(tempfile.mkdtemp()) / "usage.json"
    f.write_text(json.dumps({"pokemon": [
        {"key": "Charizard", "display": "リザードン", "species": "リザードン", "rank": 1, "moves": [], "abilities": [],
         "items": [{"name": "リザードナイトY", "en": "Charizardite Y", "pct": 90.0},
                   {"name": "リザードナイトX", "en": "Charizardite X", "pct": 8.0}]},
    ]}, ensure_ascii=False), encoding="utf-8")
    return f


class TestMega(unittest.TestCase):
    def setUp(self):
        self.cfg = {"regions": [REGION], "preview": {"icon_threshold": 0.8}}
        self.state = State(UsageDB(usage_file()), icons=IconDB(Path(tempfile.mkdtemp())))
        self.rec = Recognizer(self.state, lambda: self.cfg)
        self.rec.portraits = IconDB(Path(tempfile.mkdtemp()))
        self.rec.last_preview_seen = time.time()  # 選出画面から見ている対戦

    def feed(self, frame, n=3):
        for _ in range(n):
            self.rec.check_mega(frame, REGION, self.cfg)

    def test_mega_data(self):
        self.assertEqual(mega_forms("フラエッテ"), ["Floette-Mega"])
        self.assertEqual(mega_form_for_stone("リザードン", "Charizardite Y"), "Charizard-Mega-Y")
        self.assertEqual(mega_form_for_stone("ガブリアス", "Garchompite Z"), "Garchomp-Mega-Z")
        self.assertEqual(self.state.usage.get("Charizard")["mega"][0]["key"], "Charizard-Mega-Y")

    def test_single_mega_detected_and_learned(self):
        self.state.set_slot("opp1", ident="フラエッテ", side="opp")
        self.feed(NORMAL)  # 場に出た直後の姿を「通常」として記憶
        self.assertEqual(self.state.slots["opp1"]["ident"], "フラエッテ")
        self.feed(MEGA, 2)  # 2 フレームではまだ確定しない
        self.assertEqual(self.state.slots["opp1"]["ident"], "フラエッテ")
        self.feed(MEGA, 1)
        self.assertEqual(self.state.slots["opp1"]["ident"], "Floette-Mega")
        self.assertIn("メガフラエッテ", [m["display"] for m in self.state.snapshot([REGION])["parties"]["opp"]])
        self.assertTrue(self.rec.portraits.has_key("Floette-Mega"))  # メガの姿も覚えた

        # 次の対戦: 覚えた見た目で即座に判定できる
        self.state.reset()
        self.state.set_slot("opp1", ident="フラエッテ", side="opp")
        self.feed(MEGA)
        self.assertEqual(self.state.slots["opp1"]["ident"], "Floette-Mega")

    def test_multiple_megas_use_stone_usage(self):
        self.state.set_slot("opp1", ident="リザードン", side="opp")
        self.feed(NORMAL)
        self.feed(MEGA)
        self.assertEqual(self.state.slots["opp1"]["ident"], "Charizard-Mega-Y")  # Y 90% > X 8%
        self.assertFalse(self.rec.portraits.has_key("Charizard-Mega-Y"))  # 推定なので覚えない
        snap = self.state.snapshot([REGION])["slots"][0]
        self.assertTrue(snap["mega"])
        self.assertEqual(snap["pokemon"]["key"], "Charizard")  # 採用率は元のポケモンのデータ
        self.assertEqual(snap["form_abilities"], ["ひでり"])

    def test_no_learning_when_started_mid_battle(self):
        self.rec.last_preview_seen = 0
        self.state.set_slot("opp1", ident="フラエッテ", side="opp")
        self.feed(MEGA)  # すでにメガシンカ後かもしれないので「通常」として覚えない
        self.assertFalse(self.rec.portraits.has_key("フラエッテ"))
        self.assertEqual(self.state.slots["opp1"]["ident"], "フラエッテ")

    def test_manual_mega_updates_party(self):
        self.state.set_party("me", ["フラエッテ", "ビビヨン"])
        self.state.set_slot("me2", ident="メガフラエッテ", side="me", source="manual")
        self.assertEqual(self.state.parties["me"][0], "Floette-Mega")

    def test_my_party_restored_on_reset(self):
        self.state.set_party("me", ["フラエッテ", "ビビヨン"])
        self.state.set_slot("me1", ident="フラエッテ", side="me")
        self.state.set_form("me1", "Floette-Mega")
        self.assertEqual(self.state.parties["me"][0], "Floette-Mega")
        self.state.reset()
        self.assertEqual(self.state.parties["me"], ["フラエッテ", "ビビヨン"])


if __name__ == "__main__":
    unittest.main()
