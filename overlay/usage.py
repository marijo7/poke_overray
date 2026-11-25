"""採用率データ (data/usage.json) の読み込みと検索。"""
import json
import threading
from pathlib import Path

from .names import load_names, mega_form_for_stone, nature_by_ja, showdown_to_ja

USAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "usage.json"


class UsageDB:
    def __init__(self, path: Path = USAGE_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.meta = {}
        self.entries = {}
        self.reload()

    def reload(self):
        with self._lock:
            if not self.path.exists():
                self.meta, self.entries = {}, {}
                return False
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.meta = data.get("meta", {})
            self.entries = {e["key"]: e for e in data.get("pokemon", [])}
            stones = set(load_names().get("mega_stones", []))
            for e in self.entries.values():
                # 持ち物のメガストーン → メガシンカ率 (どのメガシンカか別)
                megas = []
                for it in e.get("items", []):
                    if it.get("en") in stones:
                        form = mega_form_for_stone(e["species"], it["en"])
                        if form:
                            megas.append({"key": form, "display": showdown_to_ja(form)[0], "pct": it["pct"]})
                e["mega"] = megas
            self._load_natures()
            return True

    # ---- 性格 ----

    def _load_natures(self):
        """データ元の性格の採用率に、上がる/下がる能力の表示を付ける (例: ようき (S↑C↓))"""
        for e in self.entries.values():
            e.setdefault("_natures_src", e.get("natures") or [])
            e["natures"] = self._decorate(e["_natures_src"])

    @staticmethod
    def _decorate(items):
        out = []
        for it in sorted(items, key=lambda x: -x["pct"]):
            n = nature_by_ja(it["name"])
            label = f"{it['name']} ({n['up']}↑{n['down']}↓)" if n and n["up"] else it["name"]
            out.append({"name": label, "nature": it["name"], "pct": float(it["pct"])})
        return out

    def get(self, key):
        return self.entries.get(key)

    def find_by_species(self, species_ja: str):
        """種族名から採用率エントリを探す。フォルム違いが複数ある場合は使用率順位が高いもの。"""
        cands = [e for e in self.entries.values() if e.get("species") == species_ja]
        if not cands:
            return None
        return min(cands, key=lambda e: (e.get("display") != species_ja, e.get("rank") or 9999))

    def roster(self):
        """対戦で使えるポケモンの一覧 (使用率順)。データ元にあれば全ポケモン、無ければ採用率データのポケモン。"""
        keys = list(self.meta.get("roster") or [])
        keys += [e["key"] for e in self.list() if e["key"] not in keys]
        return keys

    def list(self):
        return sorted(self.entries.values(), key=lambda e: e.get("rank") or 9999)
