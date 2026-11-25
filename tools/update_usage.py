"""採用率データを取得し、日本語化して data/usage.json に保存する。

データ元 (config.json の usage.source、または --source):
  championsbattledata (既定)  Pokémon Champions Battle Data の公開 API (https://championsbattledata.com/api_guide)
      ランクバトルのバトルデータ (技・持ち物・特性・性格・能力ポイント)。毎日更新。format は Doubles / Singles
      利用規約: https://championsbattledata.com/api-rules/  (表示時の出典明記が必要 → オーバーレイに表示)
  pikalytics                 Pikalytics の AI 向け公開ページ。性格はダブルには無い。format は gen9championsvgc2026regmc 等

    python tools/update_usage.py                          # config.json の設定で取得
    python tools/update_usage.py --format Singles
    python tools/update_usage.py --source pikalytics --format gen9championsou
    python tools/update_usage.py --source pikalytics --list-formats
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
from overlay.names import format_spread, nature_info, showdown_to_ja, translate  # noqa: E402

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


def parse_top_build(md):
    """「最も多い型」(性格 + 能力ポイント配分 + 割合)。フォーマットによってはデータが無い / 性格が伏せられている。"""
    m = re.search(r"features a \*\*([^*]*)\*\* nature with an EV spread of `([\d/]+)`\.\s*"
                  r"This configuration accounts for ([\d.]+)%", md)
    if not m:
        return None
    nature_en = m.group(1).strip()
    nature = nature_info(nature_en) if nature_en and set(nature_en) != {"*"} else None
    return {"nature": nature, "nature_en": nature_en if nature else None, "spread": m.group(2),
            "spread_text": format_spread(m.group(2)), "pct": float(m.group(3))}


def list_formats():
    md = get("https://www.pikalytics.com/ai/pokedex")
    for m in re.finditer(r"\*\*Format Code\*\*:\s*`([^`]+)`", md):
        print(m.group(1))
    md = get("https://www.pikalytics.com/ai")
    for code in sorted(set(re.findall(r"`((?:gen9champions|champions)[a-z0-9]*)`", md))):
        print(code)


# ---- Pokémon Champions Battle Data ----

CBD_API = "https://championsbattledata.com/api"
STAT_JA = {"HP": "H", "Attack": "A", "Defense": "B", "Sp. Atk": "C", "Sp. Def": "D", "Speed": "S"}


def get_json(url):
    return json.loads(get(url))


def cbd_entry(fmt, rank, item):
    data = get_json(f"{CBD_API}/battle/{fmt}/{urllib.parse.quote(item['showdownId'])}")
    rows = data.get("rows", [])
    pick = lambda cat: sorted((r for r in rows if r.get("category") == cat), key=lambda r: r.get("rank") or 99)
    name = item.get("battleName") or item["name"]
    display, species = showdown_to_ja(name)
    spreads = []
    for r in pick("stat_points"):
        vals = [r.get(k) or 0 for k in ("hp_points", "attack_points", "defense_points",
                                        "sp_atk_points", "sp_def_points", "speed_points")]
        spreads.append({"name": format_spread("/".join(str(int(v)) for v in vals)),
                        "spread": "/".join(str(int(v)) for v in vals), "pct": r.get("percentage_value") or 0})
    return {
        "key": name, "display": display, "species": species, "rank": rank, "usage": None,
        "moves": [{"name": translate("moves", r["name"]), "en": r["name"], "pct": r.get("percentage_value") or 0}
                  for r in pick("move")],
        "items": [{"name": translate("items", r["name"]), "en": r["name"], "pct": r.get("percentage_value") or 0}
                  for r in pick("held_item")],
        "abilities": [{"name": translate("abilities", r["name"]), "en": r["name"], "pct": r.get("percentage_value") or 0}
                      for r in pick("ability")],
        "natures": [{"name": nature_info(r["name"])["name"], "en": r["name"], "pct": r.get("percentage_value") or 0}
                    for r in pick("stat_alignment")],
        "spreads": spreads,
        "teammates": [showdown_to_ja(r["name"])[0] for r in pick("teammate")],
        "top_build": None,
    }, data.get("season")


def fetch_cbd(fmt, limit, delay):
    fmt = {"doubles": "Doubles", "singles": "Singles"}.get(fmt.lower(), "Doubles")
    rk = get_json(f"{CBD_API}/rankings?format={fmt}&limit={limit}")
    result = []
    for item in rk.get("rankings", [])[:limit]:
        time.sleep(delay)
        try:
            entry, _ = cbd_entry(fmt, item["position"], item)
        except Exception as e:  # 1件失敗しても続行
            print(f"  [{item.get('position')}] {item.get('name')}: 取得失敗 {e}")
            continue
        result.append(entry)
        print(f"  [{entry['rank']}] {entry['display']}  技{len(entry['moves'])} 持ち物{len(entry['items'])} "
              f"特性{len(entry['abilities'])} 性格{len(entry['natures'])}")
    seasons = [s for s in rk.get("seasons", []) if s != "Current"]
    meta = {
        "source": "Pokémon Champions Battle Data",
        "url": "https://championsbattledata.com/",
        "format": fmt + (f" {seasons[0]}" if seasons else ""),  # seasons は新しい順 (Current, M7, M6, ...)
        "data_date": (rk.get("generatedAt") or "")[:10] or None,
    }
    return result, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["championsbattledata", "pikalytics"], help="データ元")
    ap.add_argument("--format", help="Doubles / Singles (championsbattledata) / Pikalytics のフォーマットコード")
    ap.add_argument("--limit", type=int, help="取得するポケモン数 (既定: championsbattledata 100 / pikalytics 50)")
    ap.add_argument("--delay", type=float, default=0.5, help="リクエスト間隔(秒)")
    ap.add_argument("--list-formats", action="store_true", help="Pikalytics のフォーマット一覧")
    ap.add_argument("--out", help="保存先 (既定: data/usage.json)")
    args = ap.parse_args()

    if args.list_formats:
        list_formats()
        return

    ucfg = load_config()["usage"]
    source = args.source or ucfg.get("source", "championsbattledata")
    if source == "championsbattledata":
        fmt = args.format or ucfg.get("format") or "Doubles"
        if fmt.startswith("gen9"):  # 旧設定 (Pikalytics のコード) からの移行
            fmt = "Singles" if ("ou" in fmt or "bss" in fmt) else "Doubles"
        print(f"データ元: Pokémon Champions Battle Data / {fmt}")
        result, meta = fetch_cbd(fmt, args.limit or 100, args.delay)
    else:
        fmt = args.format or ucfg.get("pikalytics_format") or "gen9championsvgc2026regmc"
        print(f"データ元: Pikalytics / {fmt}")
        result, meta = fetch_pikalytics(fmt, args.limit or 50, args.delay)
    if not result:
        sys.exit("採用率データを取得できませんでした")

    meta["fetched_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    out = Path(args.out) if args.out else ROOT / "data" / "usage.json"
    out.write_text(json.dumps({"meta": meta, "pokemon": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"保存しました: {out} ({len(result)} 匹)")


def fetch_pikalytics(fmt, limit, delay):
    index = parse_index(get(f"{BASE}/{fmt}"))
    if not index:
        sys.exit("使用率ランキングが取得できませんでした。フォーマットコードを確認してください (--list-formats)")
    result, data_date = [], None
    for rank, name, usage in index[:limit]:
        time.sleep(delay)
        try:
            md = get(f"{BASE}/{fmt}/{urllib.parse.quote(name)}")
        except Exception as e:  # 1件失敗しても続行
            print(f"  [{rank}] {name}: 取得失敗 {e}")
            continue
        sec, date = parse_pokemon(md)
        data_date = data_date or date
        display, species = showdown_to_ja(name)
        result.append({
            "key": name, "display": display, "species": species, "rank": rank, "usage": usage,
            **{kind: [{"name": translate(kind, n), "en": n, "pct": p} for n, p in sec[kind]]
               for kind in SECTIONS.values()},
            "top_build": parse_top_build(md),
        })
        print(f"  [{rank}] {display}  技{len(sec['moves'])} 持ち物{len(sec['items'])} 特性{len(sec['abilities'])}")
    meta = {"source": "Pikalytics", "url": f"https://www.pikalytics.com/pokedex/{fmt}", "format": fmt,
            "data_date": data_date}
    return result, meta


if __name__ == "__main__":
    main()
