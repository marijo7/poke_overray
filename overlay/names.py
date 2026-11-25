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
    "F": "♀", "M": "♂", "Female": "♀", "Male": "♂", "Therian": "れいじゅう", "Eternal": "えいえんのはな", "Bloodmoon": "アカツキ",
    "Hearthflame": "かまどのめん", "Wellspring": "いどのめん", "Cornerstone": "いしずえのめん",
    "Four": "4ひきかぞく", "Three": "3びきかぞく", "Fancy": "",  # ビビヨンの模様は対戦に関係しないので省く "Dusk": "たそがれ",
    "Midnight": "まよなか", "Midday": "まひる", "Aqua": "ウォーター", "Blaze": "ブレイズ", "Combat": "コンバット",
    "Shield": "シールド", "Blade": "ブレード", "Origin": "オリジン", "Incarnate": "けしん",
}


def normalize(text: str) -> str:
    """比較用に正規化: 全角半角統一・ひらがな→カタカナ・記号/空白/数字除去。"""
    t = unicodedata.normalize("NFKC", text)
    t = _NOISE.sub("", t)
    t = t.translate(_CONFUSABLE)
    # ひらがな → カタカナ (ポケモン名はカタカナ)
    t = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in t)
    return t


# 斜体の文字などで OCR が取り違えやすい組を同一視する (比較用)
_LOOSE = str.maketrans({
    "ソ": "ン", "ツ": "シ", "ッ": "シ", "ァ": "ア", "ィ": "イ", "ゥ": "ウ", "ェ": "エ", "ォ": "オ",
    "ャ": "ヤ", "ュ": "ユ", "ョ": "ヨ", "ヮ": "ワ", "ヵ": "カ", "ヶ": "ケ",
})


def loose(t: str) -> str:
    """normalize 済みの文字列から、濁点・半濁点と取り違えやすい差を取り除く (ビ/ピ/ヒ, ン/ソ, ッ/ツ/シ 等)。"""
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if c not in "\u3099\u309a")
    return unicodedata.normalize("NFC", t).translate(_LOOSE)


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
    label = "-".join(x for x in (_FORM_JA.get(f, f) for f in form) if x)
    return (f"{ja}({label})" if label else ja), ja


def translate(kind: str, en: str) -> str:
    """kind: moves / items / abilities。辞書に無ければ英語のまま。"""
    table = load_names()[kind]
    ja = table.get(en) or table.get(en.replace("'", "’")) or table.get(en.replace("’", "'")) or en
    return unicodedata.normalize("NFKC", ja)


class NameMatcher:
    """OCR テキストからポケモン(種族)の日本語名を特定する。"""

    def __init__(self, names=None, min_ratio: float = 0.72):
        names = list(names) if names is not None else list(load_names()["pokemon"].values())
        self.min_ratio = min_ratio
        self.by_norm = {}
        for n in names:
            self.by_norm.setdefault(normalize(n), n)
        self.by_loose = {}
        for n in self.by_norm:
            self.by_loose.setdefault(loose(n), []).append(n)
        # 長い名前を優先 (ミュウツー を ミュウ より先に)
        self.sorted_norms = sorted(self.by_norm, key=len, reverse=True)

    def match(self, text: str):
        """戻り値: (日本語名, スコア 0-1) または (None, 0)。"""
        t = normalize(text)
        if len(t) < 2:
            return None, 0.0
        if t in self.by_norm:  # メガニウム・メガヤンマ はそのまま一致する
            return self.by_norm[t], 1.0
        if t.startswith("メガ") and len(t) > 3:  # メガリザードンY → リザードン
            t = t[2:]
            if t in self.by_norm:
                return self.by_norm[t], 1.0
            if t[:-1] in self.by_norm and t[-1] in "XYZ":
                return self.by_norm[t[:-1]], 1.0
        # 「あいての ガブリアス」等、余計な文字を含むケース
        for n in self.sorted_norms:
            if len(n) >= 3 and n in t:
                return self.by_norm[n], 0.95
        ratio = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio()
        # 取り違えやすい文字を同一視すると一致 (グレソアルマ → グレンアルマ, ビピヨソ → ビビヨン)
        lt = loose(t)
        if lt in self.by_loose:
            n = max(self.by_loose[lt], key=lambda n: ratio(t, n))
            return self.by_norm[n], 0.9
        best, best_score = None, 0.0
        for n in self.sorted_norms:
            if abs(len(n) - len(t)) > 3:
                continue
            # 元の文字列と、取り違えを同一視した文字列の両方で比べて高いほう
            s = max(ratio(t, n), ratio(lt, loose(n)) * 0.97)
            if s > best_score:
                best, best_score = n, s
        if best is not None and best_score >= self.min_ratio:
            return self.by_norm[best], best_score
        return None, best_score


# ---- 画像 ID (PokeAPI のポケモン ID) ----

_SUFFIX_ALIAS = {"f": "female", "m": "male"}


def _identifier(showdown: str) -> str:
    t = unicodedata.normalize("NFKD", showdown).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[.'’:]", "", t).replace(" ", "-")
    parts = t.split("-")
    if len(parts) > 1 and parts[-1] in _SUFFIX_ALIAS:
        parts[-1] = _SUFFIX_ALIAS[parts[-1]]
    return "-".join(parts)


def sprite_id_for_showdown(name: str):
    """Showdown 形式の名前 → 画像 ID。フォルム画像が無ければ通常フォルム。"""
    names = load_names()
    fid = names["forms"].get(_identifier(name))
    if fid:
        return fid
    base, _ = split_showdown_name(name)
    return names["species_id"].get(base)


@lru_cache(maxsize=1)
def _ja_to_en() -> dict:
    out = {}
    for en, ja in load_names()["pokemon"].items():
        out.setdefault(normalize(ja), en)
    return out


def sprite_id_for_ja(species_ja: str):
    """日本語の種族名 → 画像 ID (通常フォルム)。"""
    en = _ja_to_en().get(normalize(species_ja))
    return load_names()["species_id"].get(en) if en else None


def resolve(ident: str, usage_db=None):
    """パーティ等に登録された識別子 (採用率データのキー / 日本語名) → 表示用情報。"""
    if not ident:
        return None
    if ident.startswith("icon:"):  # 選出画面で見たが未登録のアイコン
        return {"key": ident, "display": "?", "species": None, "sprite": None, "icon": ident[5:],
                "has_data": False}
    entry = None
    if usage_db is not None:
        entry = usage_db.get(ident) or next(
            (e for e in usage_db.entries.values() if e["display"] == ident), None)
    if entry:
        return {"key": entry["key"], "display": entry["display"], "species": entry["species"],
                "sprite": sprite_id_for_showdown(entry["key"]), "has_data": True}
    if re.fullmatch(r"[A-Za-z0-9 .'’:-]+", ident):  # 英語 (Showdown) 名
        if usage_db is not None and usage_db.get(ident) is None:
            low = ident.lower()  # 大文字小文字違いで採用率データにあるか
            hit = next((k for k in usage_db.entries if k.lower() == low), None)
            if hit:
                return resolve(hit, usage_db)
        display, species = showdown_to_ja(ident)
        return {"key": ident, "display": display, "species": species,
                "sprite": sprite_id_for_showdown(ident), "has_data": False}
    if usage_db is not None:  # 空白・カッコの有無などの表記ゆれを吸収して採用率データと照合
        n = normalize(ident)
        entry = next((e for e in usage_db.entries.values() if normalize(e["display"]) == n), None)
        if entry:
            return resolve(entry["key"], usage_db)
    sd = ja_form_index().get(normalize(ident))
    if sd:  # メガリザードンY などフォルム付きの日本語名
        display, species = showdown_to_ja(sd)
        return {"key": sd, "display": display, "species": species,
                "sprite": sprite_id_for_showdown(sd), "has_data": False}
    return {"key": ident, "display": ident, "species": ident, "sprite": sprite_id_for_ja(ident),
            "has_data": False}


_LISTED_FORMS = ("Mega", "Alola", "Galar", "Hisui", "Paldea", "Female", "Wash", "Heat", "Frost", "Fan", "Mow",
                 "Therian", "Eternal", "Bloodmoon")


@lru_cache(maxsize=1)
def ja_form_index():
    """日本語のフォルム名 (正規化済み) → Showdown 形式の名前。メガシンカ・リージョンフォーム等。"""
    out = {}
    for ident in load_names()["forms"]:
        sd = "-".join(w.capitalize() for w in ident.split("-"))
        _, form = split_showdown_name(sd)
        if form and form[0] in _LISTED_FORMS:
            out.setdefault(normalize(showdown_to_ja(sd)[0]), sd)
    return out


@lru_cache(maxsize=1)
def all_names_ja():
    """手動入力の候補: 全種族 (図鑑順) + メガシンカ・リージョンフォーム等。"""
    names = load_names()
    species = [ja for en, ja in sorted(names["pokemon"].items(), key=lambda kv: names["species_id"].get(kv[0], 9999))]
    forms = sorted(showdown_to_ja(sd)[0] for sd in ja_form_index().values())
    return species + forms


# ---- メガシンカ ----

@lru_cache(maxsize=None)
def mega_forms(species_ja: str):
    """種族 (日本語名) のメガシンカ後フォルム (Showdown 形式) の一覧。例: リザードン → [Charizard-Mega-X, Charizard-Mega-Y]"""
    out = []
    for ident in load_names()["forms"]:
        if "-mega" not in ident:
            continue
        sd = "-".join(w.capitalize() for w in ident.split("-"))
        base, form = split_showdown_name(sd)
        if form and form[0] == "Mega" and species_ja == species_ja_of(base):
            out.append(sd)
    return sorted(out)


def species_ja_of(en_species: str) -> str:
    return load_names()["pokemon"].get(en_species, en_species)


def is_mega(key: str) -> bool:
    return bool(key) and "-Mega" in key


def mega_form_for_stone(species_ja: str, stone_en: str):
    """メガストーン → メガシンカ後のフォルム。例: Charizardite Y → Charizard-Mega-Y"""
    forms = mega_forms(species_ja)
    suffix = stone_en.rsplit(" ", 1)[-1] if stone_en[-2:-1] == " " else ""
    for f in forms:
        if (suffix and f.endswith("-Mega-" + suffix)) or (not suffix and f.endswith("-Mega")):
            return f
    return forms[0] if forms else None


def form_abilities_ja(key: str):
    """メガシンカ後フォルムの特性 (日本語)。該当しなければ空。"""
    ab = load_names().get("form_abilities", {}).get(_identifier(key), [])
    return [translate("abilities", a) for a in ab]


# ---- 種族値・性格 ----

STAT_LABELS = ["H", "A", "B", "C", "D", "S"]


def base_stats(pid):
    """画像 ID (= PokeAPI のポケモン ID) → 種族値 [H, A, B, C, D, S]。メガシンカ後のフォルムも可。"""
    return load_names().get("stats", {}).get(str(pid)) if pid else None


def nature_info(en: str):
    """性格の英語名 → {"name": 日本語名, "up": "S", "down": "C"} (無補正は up/down が None)"""
    n = load_names().get("natures", {}).get(en)
    if not n:
        return {"name": en, "up": None, "down": None}
    lab = lambda i: STAT_LABELS[i] if i is not None else None
    return {"name": n["ja"], "up": lab(n["up"]), "down": lab(n["down"])}


def format_spread(spread: str) -> str:
    """'2/32/0/0/0/32' → 'H2 A32 S32' (Champions の能力ポイント。0 は省略)"""
    try:
        vals = [int(v) for v in spread.split("/")]
    except ValueError:
        return spread
    if len(vals) != 6:
        return spread
    return " ".join(f"{STAT_LABELS[i]}{v}" for i, v in enumerate(vals) if v) or "なし"


def nature_list_ja():
    """性格の日本語名一覧 (補正の表記つき)。手入力の候補に使う。"""
    out = []
    for en in load_names().get("natures", {}):
        n = nature_info(en)
        out.append({"name": n["name"], "up": n["up"], "down": n["down"]})
    return sorted(out, key=lambda n: (n["up"] or "Z", n["down"] or "Z"))


def nature_by_ja(name: str):
    return next((n for n in nature_list_ja() if n["name"] == name), None)


# ---- 性別違い (イエッサン♀ 等) ----

def gender_variant(species_ja: str, gender, usage_db=None):
    """種族と性別 ("F"/"M") から、性別で分かれているデータのキーを返す。分かれていなければ None。

    採用率データでは メス = "<種族>-F" (Indeedee-F 等)、オス = 種族名そのもの (Indeedee)。
    """
    if not gender or usage_db is None:
        return None
    cands = [e for e in usage_db.entries.values() if e.get("species") == species_ja]
    female = [e for e in cands if e["key"].endswith("-F")]
    if not female:
        return None  # 性別でデータが分かれていない
    if gender == "F":
        return female[0]["key"]
    male = [e for e in cands if not e["key"].endswith("-F") and "-Mega" not in e["key"]]
    return male[0]["key"] if male else female[0]["key"].rsplit("-F", 1)[0]
