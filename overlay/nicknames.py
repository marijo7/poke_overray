"""ニックネームとポケモンの対応を覚える。

ポケモン名の代わりにニックネームが表示されることがある。
一度「しのみや = イエッサン(♀)」と教えれば、以後は選出画面・対戦画面とも自動で認識する。
"""
import difflib
import json
import re
import threading
from pathlib import Path

from .names import normalize

NICK_PATH = Path(__file__).resolve().parent.parent / "data" / "nicknames.json"


def nick_key(text: str) -> str:
    """比較用: 記号・空白を除き、ひらがな/カタカナ・大文字/小文字の違いを無視する。"""
    return normalize(text).lower()


def clean_nick(text: str) -> str:
    """表示用: OCR で混ざった記号 (「 」 ' 等) と前後の空白を取り除く。"""
    return re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff\uff10-\uff5a・ー]+", "", text).strip() or text.strip()


def looks_like_name(text: str) -> bool:
    """OCR のゴミ (記号だけ・1 文字だけ) でなく、名前らしい文字列か。"""
    k = nick_key(text)
    return len(k) >= 2 and sum(ch.isalpha() for ch in k) >= 2


class NicknameDB:
    def __init__(self, path: Path = NICK_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.items = {}  # key -> {"nick": 表示用, "ident": ポケモン (採用率データのキー or 日本語名)}
        if path.exists():
            self.items = json.loads(path.read_text(encoding="utf-8"))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    def set(self, nick: str, ident: str):
        k = nick_key(nick)
        if not k:
            return
        with self.lock:
            self.items[k] = {"nick": clean_nick(nick), "ident": ident}
            self._save()

    def remove(self, nick: str) -> bool:
        with self.lock:
            k = nick_key(nick)
            if k not in self.items:
                k = next((key for key, v in self.items.items() if v["nick"] == nick), None)
            if k is None:
                return False
            self.items.pop(k)
            self._save()
            return True

    def match(self, text: str, min_ratio: float = 0.8):
        """OCR 文字列 → 登録済みのポケモン。OCR の誤読 (1 文字違い・余計な記号) も許容する。"""
        k = nick_key(text)
        if len(k) < 2:
            return None
        with self.lock:
            if k in self.items:
                return self.items[k]["ident"]
            best, best_score = None, 0.0
            for key, v in self.items.items():
                if key in k and len(key) >= 3:  # 余計な文字が付いている (「しのみや「リ」 等)
                    return v["ident"]
                s = difflib.SequenceMatcher(None, k, key).ratio()
                if s > best_score:
                    best, best_score = v["ident"], s
            return best if best_score >= min_ratio else None

    def list(self):
        with self.lock:
            return [dict(v, key=k) for k, v in self.items.items()]
