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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kanji", action="store_true", help="技・持ち物・特性を漢字表記にする")
    args = ap.parse_args()

    print("PokeAPI からダウンロード中...", file=sys.stderr)
    data = {
        "source": "PokeAPI (https://pokeapi.co/)",
        "pokemon": build_map(fetch_csv("pokemon_species_names"), False),
        "moves": build_map(fetch_csv("move_names"), args.kanji),
        "items": build_map(fetch_csv("item_names"), args.kanji),
        "abilities": build_map(fetch_csv("ability_names"), args.kanji),
    }
    out = ROOT / "data" / "names.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"書き出し: {out}  (ポケモン {len(data['pokemon'])} / 技 {len(data['moves'])} / "
          f"持ち物 {len(data['items'])} / 特性 {len(data['abilities'])})", file=sys.stderr)


if __name__ == "__main__":
    main()
