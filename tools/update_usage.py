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
    python tools/update_usage.py --force                  # 今日すでに取得済みでも取得し直す

データ元に負荷をかけないよう、前回の取得以降にデータ元が更新されていなければ取得しない
(データ元の更新は毎日 04:40 UTC = 日本時間 13:40 頃。区切りは config.json の usage.update_hour_utc、既定 5 = 日本時間 14:00)。
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


ICON_DIR = ROOT / "data" / "champions_icons"


def fetch_champion_icons(roster, delay=0.1):
    """選出画面のアイコン照合に使う、Champions のポケモン画像を取得する (初回のみ。以後は増えた分だけ)。

    「新しいポケモン」= ランキング (毎回取得している) に、手元の対応表 (map.json) に無い名前が出てきたとき。
    画像のファイル名は API の一覧 (/api, 約 18MB) にあるので、そのときだけ一覧を取り直す。
    一覧を取り直してもまだ載っていなかった名前は記録しておき、7 日間は一覧を取り直さない
    (データ元の一覧への反映が遅れている間に、更新のたびに 18MB を取り直さないため)。"""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    map_path, miss_path = ICON_DIR / "map.json", ICON_DIR / "missing.json"
    m = json.loads(map_path.read_text(encoding="utf-8")) if map_path.exists() else {}
    missing = json.loads(miss_path.read_text(encoding="utf-8")) if miss_path.exists() else {}
    now = time.time()
    new = [r for r in roster if r not in m and now - missing.get(r, 0) > 7 * 86400]
    if new:
        print(f"新しいポケモン {len(new)} 匹 ({'、'.join(new[:5])}{' 等' if len(new) > 5 else ''})"
              " の画像を探すため、画像の一覧を取得中 (約 18MB)...")
        index = get_json(CBD_API)
        for e in index.get("pokemon", []):
            summ = e.get("summary") or {}
            if summ.get("sprite"):
                m[e.get("battleName") or e["name"]] = summ["sprite"]
            for f in summ.get("forms", []):
                if f.get("showdown_name") and f.get("image_path"):
                    m.setdefault(f["showdown_name"], f["image_path"])
        map_path.write_text(json.dumps(m, ensure_ascii=False, indent=0), encoding="utf-8")
        for r in new:  # まだ一覧に無かったもの → 7 日間は探し直さない
            if r in m:
                missing.pop(r, None)
            else:
                missing[r] = now
        miss_path.write_text(json.dumps(missing, ensure_ascii=False, indent=0), encoding="utf-8")
        if any(r not in m for r in new):
            print(f"  画像がまだ無いポケモン: {'、'.join(r for r in new if r not in m)} (7 日後にもう一度探します)")
    # メガシンカ後の姿も (対戦中のメガシンカの判定に使う): 名前が「<使えるポケモン>-Mega...」のもの
    bases = set(roster)
    megas = [k for k in m if "-Mega" in k and k.split("-Mega")[0] in bases]
    todo = [r for r in [*roster, *megas] if r in m and not (ICON_DIR / f"{r}.png").exists()]
    if todo:
        print(f"ポケモン画像を取得中: {len(todo)} 枚")
    for r in todo:
        try:
            req = urllib.request.Request("https://championsbattledata.com/" + urllib.parse.quote(m[r]),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                (ICON_DIR / f"{r}.png").write_bytes(resp.read())
        except Exception as e:
            print(f"  {r}: 画像の取得失敗 {e}")
        time.sleep(delay)


def fetch_cbd(fmt, limit, delay):
    fmt = {"doubles": "Doubles", "singles": "Singles"}.get(fmt.lower(), "Doubles")
    rk = get_json(f"{CBD_API}/rankings?format={fmt}")  # 全ポケモンの順位 (候補の絞り込みに使う)
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
        # 対戦で使える全ポケモン (使用率順)。選出画面のタイプからポケモンを絞り込むときの候補
        "roster": [r.get("battleName") or r["name"] for r in rk.get("rankings", [])],
    }
    return result, meta


SOURCE_NAMES = {"championsbattledata": "Pokémon Champions Battle Data", "pikalytics": "Pikalytics"}


def last_update(now: datetime.datetime, hour_utc: float) -> datetime.datetime:
    """直近のデータ更新時刻 (UTC の hour_utc 時)。データ元は毎日 04:40 UTC 頃に更新されるので、既定は余裕を見て 5:00 UTC。"""
    now = now.astimezone(datetime.timezone.utc)
    t = now.replace(hour=int(hour_utc), minute=int(hour_utc % 1 * 60), second=0, microsecond=0)
    return t if now >= t else t - datetime.timedelta(days=1)


def fetched_since_update(path: Path, source: str, fmt: str, now=None, hour_utc: float = 5.0):
    """前回の取得 (同じデータ元・形式) が、直近のデータ更新より後なら、その取得日時を返す (= 取り直しても同じデータ)。

    区切りを PC の日付ではなくデータ元の更新時刻にしているので、午前に取得しても、更新後の午後にもう一度取得できる。
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        meta = json.loads(path.read_text(encoding="utf-8")).get("meta", {})
        fetched = datetime.datetime.fromisoformat(meta["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None  # データが無い・壊れている → 取得する
    if fetched.tzinfo is None:
        fetched = fetched.astimezone()  # 以前の形式 (タイムゾーン無し) は PC の時刻として扱う
    same_source = meta.get("source_id") == source or meta.get("source") == SOURCE_NAMES[source]
    same_format = (meta.get("format_requested") or meta.get("format") or "").split(" ")[0].lower() == fmt.lower()
    if same_source and same_format and fetched >= last_update(now, hour_utc):
        return fetched
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["championsbattledata", "pikalytics"], help="データ元")
    ap.add_argument("--format", help="Doubles / Singles (championsbattledata) / Pikalytics のフォーマットコード")
    ap.add_argument("--limit", type=int, help="取得するポケモン数 (既定: championsbattledata 100 / pikalytics 50)")
    ap.add_argument("--delay", type=float, default=0.5, help="リクエスト間隔(秒)")
    ap.add_argument("--list-formats", action="store_true", help="Pikalytics のフォーマット一覧")
    ap.add_argument("--out", help="保存先 (既定: data/usage.json)")
    ap.add_argument("--force", action="store_true", help="今日すでに取得済みでも取得し直す")
    args = ap.parse_args()

    if args.list_formats:
        list_formats()
        return
    update(source=args.source, fmt=args.format, limit=args.limit, delay=args.delay,
           out=Path(args.out) if args.out else None, force=args.force)


def update(source=None, fmt=None, limit=None, delay=0.5, out=None, force=False):
    """採用率データを取得して保存する。データ元が前回の取得以降に更新されていなければ何もしない。
    戻り値: True (取得して保存した) / False (取得不要)。取得できなければ SystemExit / 例外"""
    ucfg = load_config()["usage"]
    source = source or ucfg.get("source", "championsbattledata")
    if source == "championsbattledata":
        fmt = fmt or ucfg.get("format") or "Doubles"
        if fmt.startswith("gen9"):  # 旧設定 (Pikalytics のコード) からの移行
            fmt = "Singles" if ("ou" in fmt or "bss" in fmt) else "Doubles"
        fmt = {"doubles": "Doubles", "singles": "Singles"}.get(fmt.lower(), "Doubles")
    else:
        fmt = fmt or ucfg.get("pikalytics_format") or "gen9championsvgc2026regmc"
    out = out or ROOT / "data" / "usage.json"

    hour_utc = float(ucfg.get("update_hour_utc", 5.0))
    done = None if force else fetched_since_update(out, source, fmt, hour_utc=hour_utc)
    if done:
        now = datetime.datetime.now(datetime.timezone.utc)
        nxt = (last_update(now, hour_utc) + datetime.timedelta(days=1)).astimezone()
        print(f"前回の取得 ({done.astimezone():%m/%d %H:%M}) 以降、データ元はまだ更新されていないため、取得しません。")
        print(f"次の更新: {nxt:%m/%d %H:%M} 頃。取得し直す場合は --force を付けて実行してください (update_usage.bat --force)。")
        return False

    print(f"データ元: {SOURCE_NAMES[source]} / {fmt}")
    if source == "championsbattledata":
        result, meta = fetch_cbd(fmt, limit or 100, delay)
        try:
            fetch_champion_icons(meta.get("roster", []))
        except Exception as e:  # 画像が取れなくても採用率データは保存する
            print(f"ポケモン画像を取得できませんでした: {e}")
    else:
        result, meta = fetch_pikalytics(fmt, limit or 50, delay)
    if not result:
        sys.exit("採用率データを取得できませんでした")

    meta["fetched_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")  # タイムゾーン付き
    meta["source_id"], meta["format_requested"] = source, fmt  # 次回の「今日取得済み」判定に使う
    out.write_text(json.dumps({"meta": meta, "pokemon": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"保存しました: {out} ({len(result)} 匹)")
    return True


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
