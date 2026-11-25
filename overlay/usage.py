"""採用率データ (data/usage.json) の読み込みと検索。"""
import json
import threading
from pathlib import Path

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
            return True

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
