import copy
import json
import tempfile
import unittest
from pathlib import Path

from overlay.config import DEFAULT_CONFIG, PRESETS, _fill_layout, apply_preset
from overlay.names import resolve
from overlay.nicknames import NicknameDB, clean_nick, looks_like_name
from overlay.recognizer import State
from overlay.usage import UsageDB


def usage():
    f = Path(tempfile.mkdtemp()) / "usage.json"
    base = {"moves": [], "items": [], "abilities": []}
    f.write_text(json.dumps({"pokemon": [
        {"key": "Indeedee-F", "display": "イエッサン(♀)", "species": "イエッサン", "rank": 8, **base},
        {"key": "Indeedee", "display": "イエッサン", "species": "イエッサン", "rank": 28, **base},
    ]}, ensure_ascii=False), encoding="utf-8")
    return UsageDB(f)


class TestNicknames(unittest.TestCase):
    def db(self):
        return NicknameDB(Path(tempfile.mkdtemp()) / "nicknames.json")

    def test_match_with_ocr_noise(self):
        d = self.db()
        d.set("しのみや", "Indeedee-F")
        d.set("shuji", "Kommo-o")
        for text in ("しのみや", "しのみや「", "シノミヤ", "しのみや「リ"):
            self.assertEqual(d.match(text), "Indeedee-F", text)
        self.assertEqual(d.match("Shuji "), "Kommo-o")
        self.assertIsNone(d.match("グレンアルマ"))
        self.assertIsNone(d.match("「"))

    def test_persist_and_remove(self):
        d = self.db()
        d.set("田所浩二", "ニャオニクス")
        self.assertEqual(NicknameDB(d.path).match("田所浩ニ"), "ニャオニクス")  # 漢数字/カタカナの誤読も許容
        self.assertTrue(d.remove("田所浩二"))
        self.assertIsNone(NicknameDB(d.path).match("田所浩二"))

    def test_helpers(self):
        self.assertEqual(clean_nick("しのみや「"), "しのみや")
        self.assertFalse(looks_like_name("「"))
        self.assertTrue(looks_like_name("shuji"))

    def test_label_replaces_placeholders(self):
        st = State(usage(), nicknames=self.db())
        st.set_party("me", ["nick:しのみや", "ガブリアス"])
        st.add_unknown_nick("しのみや「", "me")
        key = st.label_nickname("しのみや", "イエッサン(♀)")
        self.assertEqual(key, "Indeedee-F")
        self.assertEqual(st.parties["me"], ["Indeedee-F", "ガブリアス"])
        self.assertEqual(st.unknown_nicks, {})
        self.assertEqual(resolve("nick:しのみや")["display"], "しのみや")


class TestResetPerBattle(unittest.TestCase):
    def test_unknown_cleared_known_kept(self):
        d = NicknameDB(Path(tempfile.mkdtemp()) / "nicknames.json")
        st = State(usage(), nicknames=d)
        st.label_nickname("しのみや", "イエッサン(♀)")
        st.add_unknown_nick("たろう", "opp")
        st.reset()  # 新しい対戦
        self.assertEqual(st.unknown_nicks, {})                       # 未登録の入力欄は消える
        self.assertEqual(d.match("しのみや"), "Indeedee-F")           # 登録済みは残る


class TestPresets(unittest.TestCase):
    def test_fill_and_apply(self):
        cfg = _fill_layout(copy.deepcopy(DEFAULT_CONFIG))
        self.assertEqual(cfg["layout"], "phone")
        self.assertEqual(len(cfg["regions"]), 4)
        self.assertIn("opp_first", cfg["preview"])
        apply_preset(cfg, "switch")
        self.assertEqual(cfg["regions"], PRESETS["switch"]["regions"])
        self.assertEqual(cfg["preview"]["marker"], PRESETS["switch"]["preview"]["marker"])
        self.assertTrue(cfg["preview"]["enabled"])  # 範囲以外の設定は残る

    def test_form_names(self):
        self.assertEqual(resolve("ニャオニクス(♀)")["key"], "Meowstic-Female")
        self.assertEqual(resolve("ニャオニクス")["key"], "ニャオニクス")  # 種族名はフォルムに取られない


if __name__ == "__main__":
    unittest.main()
