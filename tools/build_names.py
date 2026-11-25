"""PokeAPI の CSV から 英語名 → 日本語名 の辞書 (data/names.json) を作る。

    python tools/build_names.py            # かな表記 (ゲームのデフォルト)
    python tools/build_names.py --kanji    # 漢字表記 (技名など。無いものはかな)

通常は同梱の data/names.json をそのまま使えばよく、実行は不要。
"""
import argparse
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
LANG_JA_KANA, LANG_EN, LANG_JA_KANJI = "1", "9", "11"
ROOT = Path(__file__).resolve().parent.parent


def fetch_csv(name):
    with urllib.request.urlopen(BASE + name + ".csv", timeout=60) as r:
        return list(csv.reader(io.StringIO(r.read().decode("utf-8"))))[1:]


def build_map(rows, kanji):
    en, kana, kj = {}, {}, {}
    for row in rows:
        id_, lang, name = row[0], row[1], row[2]
        if lang == LANG_EN:
            en[id_] = name
        elif lang == LANG_JA_KANA:
            kana[id_] = name
        elif lang == LANG_JA_KANJI:
            kj[id_] = name
    out = {}
    for id_, e in en.items():
        ja = (kj.get(id_) if kanji else None) or kana.get(id_) or kj.get(id_)
        if ja:
            out[e] = ja
    return out


def species_ids(rows):
    """英語の種族名 → 全国図鑑番号 (= 通常フォルムの画像 ID)"""
    return {row[2]: int(row[0]) for row in rows if row[1] == LANG_EN}


def form_ids(rows):
    """フォルム違いの識別子 (例 charizard-mega-y) → ポケモン ID (画像 ID)"""
    return {row[1]: int(row[0]) for row in rows if row[7] == "0"}


def id_to_en(rows):
    return {row[0]: row[2] for row in rows if row[1] == LANG_EN}


def mega_stones(item_rows, item_names_en):
    """メガストーン (アイテム分類 44) の英語名一覧"""
    return sorted(item_names_en[r[0]] for r in item_rows if r[2] == "44" and r[0] in item_names_en)


def form_abilities(pokemon_rows, pa_rows, ability_en):
    """メガシンカ後のフォルム → 特性 (英語名)。メガシンカすると特性が 1 つに決まるため表示に使う"""
    mega_ids = {r[0]: r[1] for r in pokemon_rows if "-mega" in r[1]}
    out = {}
    for r in pa_rows:
        if r[0] in mega_ids and r[1] in ability_en:
            out.setdefault(mega_ids[r[0]], []).append(ability_en[r[1]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kanji", action="store_true", help="技・持ち物・特性を漢字表記にする")
    args = ap.parse_args()

    print("PokeAPI からダウンロード中...", file=sys.stderr)
    species_rows = fetch_csv("pokemon_species_names")
    item_name_rows, ability_name_rows, pokemon_rows = fetch_csv("item_names"), fetch_csv("ability_names"), fetch_csv("pokemon")
    data = {
        "source": "PokeAPI (https://pokeapi.co/)",
        "pokemon": build_map(species_rows, False),
        "species_id": species_ids(species_rows),
        "forms": form_ids(pokemon_rows),
        "mega_stones": mega_stones(fetch_csv("items"), id_to_en(item_name_rows)),
        "form_abilities": form_abilities(pokemon_rows, fetch_csv("pokemon_abilities"), id_to_en(ability_name_rows)),
        "moves": build_map(fetch_csv("move_names"), args.kanji),
        "items": build_map(item_name_rows, args.kanji),
        "abilities": build_map(ability_name_rows, args.kanji),
    }
    out = ROOT / "data" / "names.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"書き出し: {out}  (ポケモン {len(data['pokemon'])} / 技 {len(data['moves'])} / "
          f"持ち物 {len(data['items'])} / 特性 {len(data['abilities'])})", file=sys.stderr)


if __name__ == "__main__":
    main()
