import sys
import unittest

sys.path.insert(0, "tools")
import update_usage  # noqa: E402

BATTLE = {"season": "Current", "rows": [
    {"category": "move", "rank": 2, "name": "Rock Slide", "percentage_value": 64.4},
    {"category": "move", "rank": 1, "name": "Protect", "percentage_value": 67.6},
    {"category": "held_item", "rank": 1, "name": "Garchompite Z", "percentage_value": 26.9},
    {"category": "held_item", "rank": 2, "name": "King's Rock", "percentage_value": 1.0},
    {"category": "ability", "rank": 1, "name": "Rough Skin", "percentage_value": 97.2},
    {"category": "stat_alignment", "rank": 1, "name": "Jolly", "stat_up": "Speed", "stat_down": "Sp. Atk",
     "percentage_value": 35.3},
    {"category": "stat_points", "rank": 1, "hp_points": 2, "attack_points": 32, "defense_points": 0,
     "sp_atk_points": 0, "sp_def_points": 0, "speed_points": 32, "percentage_value": 33.6},
    {"category": "teammate", "rank": 1, "name": "Charizard"},
]}


class TestChampionsBattleData(unittest.TestCase):
    def test_entry(self):
        orig = update_usage.get_json
        update_usage.get_json = lambda url: BATTLE
        try:
            e, _ = update_usage.cbd_entry("Doubles", 3, {"name": "Garchomp", "battleName": "Garchomp",
                                                         "showdownId": "garchomp"})
        finally:
            update_usage.get_json = orig
        self.assertEqual((e["key"], e["display"], e["rank"]), ("Garchomp", "ガブリアス", 3))
        self.assertEqual([m["name"] for m in e["moves"]], ["まもる", "いわなだれ"])  # 順位順
        self.assertEqual([i["name"] for i in e["items"]], ["ガブリアスナイトZ", "おうじゃのしるし"])
        self.assertEqual(e["natures"][0], {"name": "ようき", "en": "Jolly", "pct": 35.3})
        self.assertEqual(e["spreads"][0]["name"], "H2 A32 S32")
        self.assertEqual(e["teammates"], ["リザードン"])


if __name__ == "__main__":
    unittest.main()
