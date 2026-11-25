"""Pikalytics の公開AI向けデータから採用率を取得し、日本語化して data/usage.json に保存する。

    python tools/update_usage.py                       # config.json の usage.format を使用
    python tools/update_usage.py --format gen9championsou
    python tools/update_usage.py --list-formats        # 利用可能なフォーマット一覧

データ出典: Pikalytics (https://www.pikalytics.com/)  ※オーバーレイ上に出典を表示します
"""
import argparse
import datetime
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from overlay.config import load_config  # noqa: E402
from overlay.names import showdown_to_ja, translate  # noqa: E402

BASE = "https://www.pikalytics.com/ai/pokedex"
UA = "poke-overlay/1.0 (personal OBS overlay)"
SECTIONS = {"Common Moves": "moves", "Common Abilities": "abilities", "Common Items": "items"}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def parse_index(md):
    """'Best 50 Pokemon by Usage' 表 → [(rank, name, usage%), ...]"""
    rows = []
    for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*\*\*(.+?)\*\*\s*\|\s*([\d.]+|N/A)%?\s*\|", md, re.M):
        usage = None if m.group(3) == "N/A" else float(m.group(3))
        rows.append((int(m.group(1)), m.group(2).strip(), usage))
    return rows


def parse_pokemon(md):
    out = {v: [] for v in SECTIONS.values()}
    current = None
    for line in md.splitlines():
        if line.startswith("## "):
            current = SECTIONS.get(line[3:].strip())
            continue
        if current:
            m = re.match(r"^- \*\*(.+?)\*\*:\s*([\d.]+)%", line)
            if m:
                out[current].append((m.group(1), float(m.group(2))))
    m = re.search(r"\*\*Data Date\*\*\s*\|\s*([^|]+?)\s*\|", md)
    return out, (m.group(1) if m else None)


def list_formats():
    md = get("https://www.pikalytics.com/ai/pokedex")
    for m in re.finditer(r"\*\*Format Code\*\*:\s*`([^`]+)`", md):
        print(m.group(1))
    md = get("https://www.pikalytics.com/ai")
    for code in sorted(set(re.findall(r"`((?:gen9champions|champions)[a-z0-9]*)`", md))):
        print(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--format", help="Pikalytics のフォーマットコード")
    ap.add_argument("--limit", type=int, default=50, help="取得するポケモン数 (最大50)")
    ap.add_argument("--delay", type=float, default=1.0, help="リクエスト間隔(秒)")
    ap.add_argument("--list-formats", action="store_true")
    args = ap.parse_args()

    if args.list_formats:
        list_formats()
        return

    fmt = args.format or load_config()["usage"]["format"]
    print(f"フォーマット: {fmt}")
    index = parse_index(get(f"{BASE}/{fmt}"))
    if not index:
        sys.exit("使用率ランキングが取得できませんでした。フォーマットコードを確認してください (--list-formats)")

    result, data_date = [], None
    for rank, name, usage in index[: args.limit]:
        time.sleep(args.delay)
        try:
            md = get(f"{BASE}/{fmt}/{urllib.parse.quote(name)}")
        except Exception as e:  # 1件失敗しても続行
            print(f"  [{rank}] {name}: 取得失敗 {e}")
            continue
        sec, date = parse_pokemon(md)
        data_date = data_date or date
        display, species = showdown_to_ja(name)
        result.append({
            "key": name,
            "display": display,
            "species": species,
            "rank": rank,
            "usage": usage,
            **{kind: [{"name": translate(kind, n), "en": n, "pct": p} for n, p in sec[kind]]
               for kind in SECTIONS.values()},
        })
        print(f"  [{rank}] {display}  技{len(sec['moves'])} 持ち物{len(sec['items'])} 特性{len(sec['abilities'])}")

    data = {
        "meta": {
            "source": "Pikalytics",
            "url": f"https://www.pikalytics.com/pokedex/{fmt}",
            "format": fmt,
            "data_date": data_date,
            "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        "pokemon": result,
    }
    out = ROOT / "data" / "usage.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"保存しました: {out} ({len(result)} 匹)")


if __name__ == "__main__":
    main()
