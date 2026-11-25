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


class TestFetchOncePerUpdate(unittest.TestCase):
    """区切りはデータ元の更新時刻 (既定 5:00 UTC = 日本時間 14:00)"""

    def write(self, meta):
        import json
        import tempfile
        from pathlib import Path
        f = Path(tempfile.mkdtemp()) / "usage.json"
        f.write_text(json.dumps({"meta": meta, "pokemon": []}), encoding="utf-8")
        return f

    def jst(self, s):
        import datetime
        return datetime.datetime.fromisoformat(s + "+09:00")

    def check(self, fetched, now, source="championsbattledata", fmt="Doubles"):
        f = self.write({"source_id": "championsbattledata", "format_requested": "Doubles", "fetched_at": fetched})
        return update_usage.fetched_since_update(f, source, fmt, now=self.jst(now))

    def test_boundary_is_update_time(self):
        # 午前に取得 → 午後 (更新後) はもう一度取得できる
        self.assertIsNotNone(self.check("2026-11-25T09:00:00+09:00", "2026-11-25T12:00"))  # まだ更新前 → 取得しない
        self.assertIsNone(self.check("2026-11-25T09:00:00+09:00", "2026-11-25T15:00"))     # 更新後 → 取得する
        # 午後に取得 → 翌日の午前 (次の更新前) は取得しない、翌日午後は取得する
        self.assertIsNotNone(self.check("2026-11-25T15:00:00+09:00", "2026-11-26T10:00"))
        self.assertIsNone(self.check("2026-11-25T15:00:00+09:00", "2026-11-26T14:30"))

    def test_source_and_format(self):
        self.assertIsNone(self.check("2026-11-25T15:00:00+09:00", "2026-11-25T16:00", fmt="Singles"))
        self.assertIsNone(self.check("2026-11-25T15:00:00+09:00", "2026-11-25T16:00", source="pikalytics"))

    def test_old_meta_and_missing_file(self):
        import datetime
        from pathlib import Path
        # 以前の形式 (source_id もタイムゾーンも無い) でも判定できる
        f = self.write({"source": "Pokémon Champions Battle Data", "format": "Doubles M7",
                        "fetched_at": datetime.datetime.now().isoformat(timespec="seconds")})
        self.assertIsNotNone(update_usage.fetched_since_update(f, "championsbattledata", "Doubles"))
        self.assertIsNone(update_usage.fetched_since_update(Path("/nonexistent/usage.json"), "championsbattledata",
                                                            "Doubles"))
