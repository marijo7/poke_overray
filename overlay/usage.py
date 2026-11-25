"""採用率データ (data/usage.json) の読み込みと検索。"""
import json
import threading
from pathlib import Path

from .names import load_names, mega_form_for_stone, nature_by_ja, showdown_to_ja

USAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "usage.json"
# 手入力した性格の採用率。update_usage で上書きされないよう別ファイルに保存する
NATURES_PATH = USAGE_PATH.parent / "natures.json"


class UsageDB:
    def __init__(self, path: Path = USAGE_PATH, natures_path: Path = None):
        self.path = path
        self.natures_path = natures_path or path.parent / "natures.json"
        self.manual_natures = {}
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

    # ---- 性格 (手入力) ----

    def _load_natures(self):
        self.manual_natures = {}
        if self.natures_path.exists():
            self.manual_natures = json.loads(self.natures_path.read_text(encoding="utf-8"))
        for key, e in self.entries.items():
            e["natures"] = self._decorate(self.manual_natures.get(key, []))

    @staticmethod
    def _decorate(items):
        out = []
        for it in sorted(items, key=lambda x: -x["pct"]):
            n = nature_by_ja(it["name"])
            label = f"{it['name']} ({n['up']}↑{n['down']}↓)" if n and n["up"] else it["name"]
            out.append({"name": label, "nature": it["name"], "pct": float(it["pct"])})
        return out

    def set_natures(self, key, items):
        """items: [{"name": "ようき", "pct": 60.0}, ...]。空なら削除。"""
        clean = [{"name": str(i["name"]), "pct": float(i["pct"])} for i in items
                 if i.get("name") and nature_by_ja(str(i["name"])) and float(i.get("pct") or 0) > 0]
        with self._lock:
            if clean:
                self.manual_natures[key] = clean
            else:
                self.manual_natures.pop(key, None)
            self.natures_path.write_text(json.dumps(self.manual_natures, ensure_ascii=False, indent=1), encoding="utf-8")
            if key in self.entries:
                self.entries[key]["natures"] = self._decorate(clean)
        return clean

    def get(self, key):
        return self.entries.get(key)

    def find_by_species(self, species_ja: str):
        """種族名から採用率エントリを探す。フォルム違いが複数ある場合は使用率順位が高いもの。"""
        cands = [e for e in self.entries.values() if e.get("species") == species_ja]
        if not cands:
            return None
        return min(cands, key=lambda e: (e.get("display") != species_ja, e.get("rank") or 9999))

    def list(self):
        return sorted(self.entries.values(), key=lambda e: e.get("rank") or 9999)
