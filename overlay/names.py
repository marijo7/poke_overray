"""日本語名辞書と、OCR 結果からポケモン名を特定するあいまい一致。"""
import difflib
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# OCR でよく取り違える 漢字・記号 → カタカナ
_CONFUSABLE = str.maketrans({
    "一": "ー", "−": "ー", "－": "ー", "-": "ー", "—": "ー", "―": "ー", "ｰ": "ー", "~": "ー", "〜": "ー",
    "口": "ロ", "力": "カ", "工": "エ", "二": "ニ", "夕": "タ", "卜": "ト", "八": "ハ", "才": "オ",
    "千": "チ",
})
_NOISE = re.compile(r"[\s　・･.,、。:：;；!！?？'\"“”‘’()（）\[\]「」『』♂♀♪/\\|_]|[Ll][Vv]\.?\s*\d+|\d{2,}")  # ポリゴン2 の 1 桁は残す

# Showdown 形式のフォルム名 → 日本語
_FORM_JA = {
    "Alola": "アローラ", "Galar": "ガラル", "Hisui": "ヒスイ", "Paldea": "パルデア",
    "Wash": "ウォッシュ", "Heat": "ヒート", "Frost": "フロスト", "Fan": "スピン", "Mow": "カット",
    "F": "♀", "M": "♂", "Therian": "れいじゅう", "Eternal": "えいえんのはな", "Bloodmoon": "アカツキ",
    "Hearthflame": "かまどのめん", "Wellspring": "いどのめん", "Cornerstone": "いしずえのめん",
}


def normalize(text: str) -> str:
    """比較用に正規化: 全角半角統一・ひらがな→カタカナ・記号/空白/数字除去。"""
    t = unicodedata.normalize("NFKC", text)
    t = _NOISE.sub("", t)
    t = t.translate(_CONFUSABLE)
    # ひらがな → カタカナ (ポケモン名はカタカナ)
    t = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in t)
    return t


@lru_cache(maxsize=1)
def load_names() -> dict:
    return json.loads((DATA_DIR / "names.json").read_text(encoding="utf-8"))


def _norm_en(s: str) -> str:
    return s.lower().replace("’", "'").replace("é", "e")


@lru_cache(maxsize=1)
def _en_species() -> dict:
    return {_norm_en(en): en for en in load_names()["pokemon"]}


def split_showdown_name(name: str):
    """'Charizard-Mega-Y' -> ('Charizard', ['Mega', 'Y'])。'Kommo-o' のような種族名中のハイフンも考慮。"""
    parts = name.split("-")
    table = _en_species()
    for i in range(len(parts), 0, -1):
        cand = "-".join(parts[:i])
        if _norm_en(cand) in table:
            return table[_norm_en(cand)], parts[i:]
        # 'Mr. Mime' 等の空白入り
        if _norm_en(cand.replace("-", " ")) in table:
            return table[_norm_en(cand.replace("-", " "))], parts[i:]
    return name, []


def species_ja(en_species: str) -> str:
    return load_names()["pokemon"].get(en_species, en_species)


def showdown_to_ja(name: str):
    """Showdown 形式の名前を (表示名, 種族の日本語名) に変換。"""
    base, form = split_showdown_name(name)
    ja = species_ja(base)
    if not form:
        return ja, ja
    if form[0] == "Mega":
        return "メガ" + ja + "".join(form[1:]), ja
    label = "-".join(_FORM_JA.get(f, f) for f in form)
    return f"{ja}({label})", ja


def translate(kind: str, en: str) -> str:
    """kind: moves / items / abilities。辞書に無ければ英語のまま。"""
    return unicodedata.normalize("NFKC", load_names()[kind].get(en, en))


class NameMatcher:
    """OCR テキストからポケモン(種族)の日本語名を特定する。"""

    def __init__(self, names=None, min_ratio: float = 0.72):
        names = list(names) if names is not None else list(load_names()["pokemon"].values())
        self.min_ratio = min_ratio
        self.by_norm = {}
        for n in names:
            self.by_norm.setdefault(normalize(n), n)
        # 長い名前を優先 (ミュウツー を ミュウ より先に)
        self.sorted_norms = sorted(self.by_norm, key=len, reverse=True)

    def match(self, text: str):
        """戻り値: (日本語名, スコア 0-1) または (None, 0)。"""
        t = normalize(text)
        if t.startswith("メガ") and len(t) > 3:
            t = t[2:]
        if len(t) < 2:
            return None, 0.0
        if t in self.by_norm:
            return self.by_norm[t], 1.0
        # 「あいての ガブリアス」等、余計な文字を含むケース
        for n in self.sorted_norms:
            if len(n) >= 3 and n in t:
                return self.by_norm[n], 0.95
        best, best_score = None, 0.0
        for n in self.sorted_norms:
            if abs(len(n) - len(t)) > 3:
                continue
            s = difflib.SequenceMatcher(None, t, n).ratio()
            if s > best_score:
                best, best_score = n, s
        if best is not None and best_score >= self.min_ratio:
            return self.by_norm[best], best_score
        return None, best_score
