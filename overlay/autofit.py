"""読み取り範囲の自動調整 (設定画面の「今の画面から自動で合わせる」ボタン)。

機種によって画面の端の余白や表示の大きさが違い、名前欄などの位置がずれる。
基準の配置 (プリセット) を、今の画面で見つけた目印に合わせて移動・拡大縮小する。

選出画面:
  - 相手の列: 赤い枠 (パネル) を色で探す → 列の左端と幅、1 段目の上端と段の間隔
  - 自分の列: 名前の文字の位置 (文字を位置付きで読む) → 左端と 1 段目の高さ
  - マーカー: 「選出してください」の文字の右端
対戦画面:
  - 名前欄の文字の位置を探し、名前が最後まで読める範囲の真ん中に合わせる
  - 名前欄 2 つの間隔から大きさを決め、顔画像・性別マークの範囲も一緒に動かす
"""
import logging
import re
import statistics
from pathlib import Path

import numpy as np
from PIL import Image

from .gender import detect_gender
from .nicknames import looks_like_name, nick_key
from .ocr import preprocess

log = logging.getLogger(__name__)

DET_H = 1080  # 文字の位置を探すときは、この高さに縮小して読む (4K でも時間がかからないように)
UI_WORDS = ("COMMAND", "ログを見る", "様子を見る", "たたかう", "ポケモン", "状態", "つよさ", "選出", "ランクバトル",
            "ダブルバトル", "シングルバトル")


# ---- 目印を探す ----

def detect_lines(ocr, frame):
    """画面全体の文字を位置付きで読む。戻り値: [{"text", "x0", "y0", "x1", "y1"}] (割合座標)"""
    W, H = frame.size
    k = min(1.0, DET_H / H)
    img = frame.convert("RGB")
    if k < 1:
        img = img.resize((round(W * k), round(H * k)), Image.BILINEAR)
    out = []
    for text, (x0, y0, x1, y1) in ocr.detect(img):
        out.append({"text": text, "x0": float(x0) / k / W, "y0": float(y0) / k / H,
                    "x1": float(x1) / k / W, "y1": float(y1) / k / H})
    return out


def _cy(line):
    return (line["y0"] + line["y1"]) / 2


def find_opp_panels(frame, x_from=0.5):
    """選出画面の相手の列 (赤いパネル 6 段) を色で探す。
    戻り値: {"x0", "x1", "top0", "pitch", "h", "rows"} (割合座標) / 見つからなければ None"""
    W, H = frame.size
    a = np.asarray(frame.convert("RGB"), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    # 実測: パネルの赤 (R≈120, G≈5, B≈45)。背景の赤い光の線は細いので列の割合で除く
    m = (r > 90) & (r > 2 * g) & (r > 1.3 * b) & (g < 70)
    m[:, : int(W * x_from)] = False
    col = m[int(0.1 * H): int(0.9 * H)].mean(axis=0)
    xs = np.where(col > 0.35)[0]
    if len(xs) < W * 0.03:
        return None
    # いちばん長く続く範囲 (列) を採る
    runs, s = [], xs[0]
    for p, q in zip(xs, xs[1:]):
        if q - p > 0.04 * W:  # ポケモンの絵の所で途切れるので、少しの切れ目はつなげる
            runs.append((s, p))
            s = q
    runs.append((s, xs[-1]))
    x0, x1 = max(runs, key=lambda t: t[1] - t[0])
    if x1 - x0 < W * 0.03:
        return None
    # 段: パネルの左側の細い帯 (ポケモンの絵が掛からない所) で、上下の切れ目を探す
    band = m[:, x0 + int(0.03 * (x1 - x0)): x0 + int(0.12 * (x1 - x0)) + 1].mean(axis=1)
    rows, s = [], None
    for i in range(H + 1):
        on = i < H and band[i] > 0.6
        if on and s is None:
            s = i
        elif not on and s is not None:
            rows.append((s, i - s))
            s = None
    rows = [t for t in rows if t[1] > 0.03 * H]
    if len(rows) < 3:
        return None
    hmed = statistics.median(t[1] for t in rows)
    rows = [t for t in rows if abs(t[1] - hmed) < 0.2 * hmed]
    diffs = [q[0] - p[0] for p, q in zip(rows, rows[1:])]
    unit = min(diffs)
    pitch = statistics.median(d / max(1, round(d / unit)) for d in diffs)
    top0 = rows[0][0]
    return {"x0": x0 / W, "x1": (x1 + 1) / W, "top0": top0 / H, "pitch": pitch / H, "h": hmed / H,
            "rows": len(rows)}


def find_my_names(lines, aspect, x_to=0.45, y_from=0.08, y_to=0.85):
    """選出画面の自分の列の名前 (各段の上の行) を探す。持ち物の行は少し右から始まるので、左端の揃った行だけを採る。"""
    cand = [l for l in lines if l["x0"] < x_to and y_from < _cy(l) < y_to and len(nick_key(l["text"])) >= 2]
    if len(cand) < 3:
        return []
    th = statistics.median(l["y1"] - l["y0"] for l in cand)
    # 左端がいちばん多く揃っている位置
    tol = 0.4 * th / aspect  # 文字の高さの 4 割 (横方向の割合に直す)
    count = {id(c): sum(1 for l in cand if abs(l["x0"] - c["x0"]) < tol) for c in cand}
    top = max(count.values())
    # 持ち物の行 (と「0/4」) も同じくらい揃っているので、十分揃っている中でいちばん左のものを名前とする
    best = min((c for c in cand if count[id(c)] >= max(3, top - 2)), key=lambda c: c["x0"])
    names = [l for l in cand if abs(l["x0"] - best["x0"]) < tol]
    return sorted(names, key=_cy)


def find_marker_line(lines):
    """「選出してください」の行"""
    hits = [l for l in lines if "選出" in l["text"] and "完了" not in l["text"]]
    hits += [l for l in lines if "ください" in l["text"] and l not in hits]
    return min(hits, key=lambda l: l["y0"]) if hits else None


# ---- 座標の変換 ----

class Mapper:
    """基準の画面 (ref) の位置 → 今の画面の位置。基準の目印 (ax_ref, ay_ref) が今の画面の (ax, ay) にあり、大きさが s 倍。
    座標は ref は基準画面の px、結果は今の画面の割合座標。"""

    def __init__(self, ref_size, size, s, anchor_ref, anchor):
        self.rw, self.rh = ref_size
        self.W, self.H = size
        self.s = s
        self.axr, self.ayr = anchor_ref
        self.ax, self.ay = anchor

    def rect(self, r):
        x = self.ax + self.s * (r["x"] * self.rw - self.axr)
        y = self.ay + self.s * (r["y"] * self.rh - self.ayr)
        return {"x": round(float(x / self.W), 4), "y": round(float(y / self.H), 4),
                "w": round(float(self.s * r["w"] * self.rw / self.W), 4),
                "h": round(float(self.s * r["h"] * self.rh / self.H), 4)}


OPP_KEYS = ("opp_first", "opp_last", "opp_gender_first", "opp_gender_last",
            "opp_type1_first", "opp_type1_last", "opp_type2_first", "opp_type2_last")
MY_KEYS = ("my_first", "my_last", "my_gender_first", "my_gender_last", "my_icon_first", "my_icon_last")


def fit_preview(frame, lines, preset):
    """選出画面の範囲を合わせる。戻り値: (新しい preview の範囲 dict, メッセージのリスト) / 合わせられなければ (None, 理由)"""
    ref = preset.get("fit") or {}
    pv_ref = preset["preview"]
    rw, rh = ref["ref_size"]
    W, H = frame.size
    msgs = []
    marker = find_marker_line(lines)
    if marker is None:
        return None, ["「選出してください」の文字が見つかりませんでした (選出画面を表示してから押してください)"]
    panels = find_opp_panels(frame)
    names = find_my_names(lines, W / H)
    out = {}
    s = None
    if panels:
        rp = ref["opp_panel"]
        s = panels["pitch"] * H / (rp["pitch"] * rh)
        sw = (panels["x1"] - panels["x0"]) * W / ((rp["x1"] - rp["x0"]) * rw)
        if abs(sw - s) / s > 0.12:  # 幅と段の間隔で大きさが食い違う (何かが重なっている) → 幅を優先
            s = sw
        m = Mapper(ref["ref_size"], frame.size, s, (rp["x0"] * rw, rp["top0"] * rh),
                   (panels["x0"] * W, panels["top0"] * H))
        for k in OPP_KEYS:
            if k in pv_ref:
                out[k] = m.rect(pv_ref[k])
        msgs.append(f"相手の列: 見つかりました ({panels['rows']} 段)")
    else:
        msgs.append("相手の列: 赤い枠が見つかりませんでした (基準の位置のまま)")
    if names:
        rn = ref["my_name"]
        if s is None and len(names) >= 2:
            d = [(_cy(b) - _cy(a)) * H for a, b in zip(names, names[1:])]
            unit = min(d)
            s = statistics.median(x / max(1, round(x / unit)) for x in d) / (ref["opp_panel"]["pitch"] * rh)
        if s is None:
            s = H / rh
        pitch = ref["opp_panel"]["pitch"] * rh * s
        # いちばん上に見つかった名前が何段目か (相手の列が見つかっていれば、その 1 段目の高さから)
        cy0 = _cy(names[0]) * H
        if panels:
            row0 = panels["top0"] * H + s * (rn["cy0"] - ref["opp_panel"]["top0"]) * rh
            idx = max(0, round((cy0 - row0) / pitch))
        else:
            idx = 0
        # それぞれの名前の高さから、1 段目の高さを逆算した値の中央値
        ys = [_cy(l) * H - pitch * (idx + round((_cy(l) * H - cy0) / pitch)) for l in names]
        m = Mapper(ref["ref_size"], frame.size, s, (rn["x0"] * rw, rn["cy0"] * rh),
                   (statistics.median(l["x0"] for l in names) * W, statistics.median(ys)))
        for k in MY_KEYS:
            if k in pv_ref:
                out[k] = m.rect(pv_ref[k])
        msgs.append(f"自分の列: 見つかりました (名前 {len(names)} 個)")
    else:
        msgs.append("自分の列: 名前の文字が見つかりませんでした (基準の位置のまま)")
    if s is None:
        s = H / rh
    rm = ref["marker"]
    m = Mapper(ref["ref_size"], frame.size, s, (rm["x1"] * rw, rm["cy"] * rh), (marker["x1"] * W, _cy(marker) * H))
    out["marker"] = m.rect(pv_ref["marker"])
    msgs.insert(0, "選出画面の文字: 見つかりました")
    return out, msgs


def _lerp(a, b, i, n=6):
    t = i / (n - 1)
    return {k: a[k] + (b[k] - a[k]) * t for k in ("x", "y", "w", "h")}


def preview_quality(frame, pv):
    """合わせた範囲の確からしさ: 性別マークの範囲 (自分・相手 6 段ずつ) に実際にマークが写っている数"""
    W, H = frame.size
    n = 0
    for side in ("my", "opp"):
        a, b = pv.get(f"{side}_gender_first"), pv.get(f"{side}_gender_last")
        if not a or not b:
            continue
        for i in range(6):
            r = _lerp(a, b, i)
            box = (int(r["x"] * W), int(r["y"] * H), int((r["x"] + r["w"]) * W), int((r["y"] + r["h"]) * H))
            if box[2] - box[0] >= 2 and box[3] - box[1] >= 2 and detect_gender(frame.crop(box)):
                n += 1
    return n


# ---- 対戦画面 ----
#
# 名前は欄から多少はみ出しても読めてしまうので、文字を読むだけでは位置が決まらない。
# そこで、パネルの枠の形 (名前・顔画像・HP など毎回変わる所は除いた画像) を基準の画面から切り出しておき、
# 今の画面で一致する位置と大きさを探す。

FIT_DIR = Path(__file__).resolve().parent.parent / "data" / "fit"
TPL_H = 40   # パネルの見本の高さ (縮小後 px)


def panel_box(r):
    """名前欄・顔画像・性別マークを囲む範囲 (割合座標 x0, y0, x1, y1)"""
    p, g = r["portrait"], r["gender"]
    return (min(p["x"], r["x"]), min(p["y"], r["y"]), max(r["x"] + r["w"], g["x"] + g["w"]),
            max(p["y"] + p["h"], r["y"] + r["h"]))


def make_panel_template(ref_img, r):
    """基準の画面からパネルの見本を作る。毎回変わる所 (顔画像・名前・性別・HP) は重み 0 にして灰色で塗る。
    戻り値: dict(tpl, mask, box=(基準画面 px の左上 x, y), k=縮小率)"""
    W, H = ref_img.size
    x0, y0, x1, y1 = panel_box(r)
    pad = 0.15 * (y1 - y0) * H
    box = (x0 * W - pad, y0 * H - pad, x1 * W + pad, y1 * H + pad)
    crop = ref_img.convert("RGB").crop(tuple(int(v) for v in box))
    k = TPL_H / crop.height
    t = np.asarray(crop.resize((round(crop.width * k), TPL_H), Image.BILINEAR), np.float32)
    mask = np.ones(t.shape[:2], np.float32)

    def cut(q, grow=0.0):
        qx0, qy0 = (q["x"] * W - int(box[0])) * k, (q["y"] * H - int(box[1])) * k
        qx1, qy1 = qx0 + q["w"] * W * k, qy0 + q["h"] * H * k
        gx, gy = (qx1 - qx0) * grow, (qy1 - qy0) * grow
        mask[max(0, int(qy0 - gy)): int(qy1 + gy) + 1, max(0, int(qx0 - gx)): int(qx1 + gx) + 1] = 0

    cut(r["portrait"], 0.05)
    cut(r, 0.05)
    cut(r["gender"], 0.2)
    p = r["portrait"]
    cut({"x": r["x"], "y": r["y"] + r["h"], "w": r["w"] + r["gender"]["w"] * 2,
         "h": p["y"] + p["h"] - r["y"] - r["h"]})  # HP バー
    t[mask == 0] = 128
    return {"tpl": t.astype(np.uint8), "mask": mask.astype(np.uint8), "box": (int(box[0]), int(box[1])), "k": k}


def save_templates(layout, ref_img, regions):
    FIT_DIR.mkdir(parents=True, exist_ok=True)
    for r in regions:
        t = make_panel_template(ref_img, r)
        np.savez_compressed(FIT_DIR / f"{layout}_{r['id']}.npz", tpl=t["tpl"], mask=t["mask"],
                            box=np.array(t["box"]), k=np.array(t["k"]))


def load_template(layout, rid):
    p = FIT_DIR / f"{layout}_{rid}.npz"
    if not p.exists():
        return None
    z = np.load(p)
    return {"tpl": z["tpl"].astype(np.float32), "mask": z["mask"].astype(np.float32),
            "box": tuple(int(v) for v in z["box"]), "k": float(z["k"])}


def _ncc_search(area, tpl, mask):
    """area (H, W, 3) の中で、重み付きの正規化相互相関がいちばん高い位置。戻り値: (相関, x, y)"""
    th, tw = tpl.shape[:2]
    if area.shape[0] < th or area.shape[1] < tw:
        return -2.0, 0, 0
    win = np.lib.stride_tricks.sliding_window_view(area, (th, tw, 3))[:, :, 0]
    w = mask[None, None, :, :, None]
    ws = mask.sum()
    tm = (tpl * mask[..., None]).sum(axis=(0, 1)) / ws
    tc = (tpl - tm) * mask[..., None]
    mu = (win * w).sum(axis=(2, 3)) / ws
    num = np.einsum("yxhwc,hwc->yx", win, tc)  # tc は重み 0 の所が 0 なので、窓の平均を引かなくても同じ
    var = np.einsum("yxhwc,hw->yx", win * win, mask) - ws * (mu ** 2).sum(axis=-1)
    c = num / (np.sqrt(np.maximum(var, 1e-6) * (tc ** 2).sum()) + 1e-6)
    j = np.unravel_index(int(c.argmax()), c.shape)
    return float(c[j]), int(j[1]), int(j[0])


def match_panel(frame, t, cx, cy, s0, span, spread=0.3):
    """パネルの見本 t を、今の画面の (cx, cy) (px, 見本の左上の予想位置) 付近で探す。
    s0: 予想の大きさ (基準画面の何倍か)。粗く (大きさ ±30%) 探してから、細かく合わせる。
    戻り値: (相関, s, 左上 x, 左上 y) (px)"""
    tpl, mask, k = t["tpl"], t["mask"], t["k"]
    th, tw = tpl.shape[:2]
    best = (-2.0, s0, cx, cy)

    def run(tpl_, mask_, kk, scales, cx_, cy_, sx, sy):
        nonlocal best
        th_, tw_ = tpl_.shape[:2]
        for s in scales:
            f = kk / s  # 今の画面の 1px → 見本の縮尺
            x0, y0 = cx_ - sx, cy_ - sy
            box = (int(x0), int(y0), int(cx_ + sx + tw_ / f) + 1, int(cy_ + sy + th_ / f) + 1)
            crop = frame.crop(box)
            a = np.asarray(crop.resize((max(1, round(crop.width * f)), max(1, round(crop.height * f))),
                                       Image.BILINEAR), np.float32)
            c, x, y = _ncc_search(a, tpl_, mask_)
            if c > best[0]:
                best = (c, s, box[0] + x / f, box[1] + y / f)

    # 粗く: 見本を半分に縮めて、大きさ 4% 刻み
    small = Image.fromarray(tpl.astype(np.uint8)).resize((max(1, tw // 2), max(1, th // 2)), Image.BILINEAR)
    msmall = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).resize(small.size, Image.BILINEAR),
                        np.float32) / 255
    run(np.asarray(small, np.float32), (msmall > 0.99).astype(np.float32), k / 2,
        [s0 * (1 - spread + 0.04 * i) for i in range(int(spread * 50) + 1)] or [s0], cx, cy, span, span * 0.5)
    # 細かく: 見つかった大きさ ±4% を 1% 刻み、位置 ±(粗い 1 目盛り)
    _, s1, x1, y1 = best
    fine = [s1 * (0.96 + 0.01 * i) for i in range(9)] if spread >= 0.04 else [s1 * (0.99 + 0.01 * i) for i in range(3)]
    run(tpl, mask, k, fine, x1, y1, 3 * s1 / k, 3 * s1 / k)
    return best


# ---- 対戦画面かどうか (認識ループで毎フレーム使う) ----

_TPL_CACHE = {}


def _template_small(layout, rid):
    """見本を半分の大きさにしたもの (毎フレームの判定を軽くするため)"""
    key = (layout, rid)
    if key not in _TPL_CACHE:
        t = load_template(layout, rid)
        if t is not None:
            th, tw = t["tpl"].shape[:2]
            size = (max(1, tw // 2), max(1, th // 2))
            tpl = np.asarray(Image.fromarray(t["tpl"].astype(np.uint8)).resize(size, Image.BILINEAR), np.float32)
            m = np.asarray(Image.fromarray((t["mask"] * 255).astype(np.uint8)).resize(size, Image.BILINEAR),
                           np.float32) / 255
            t = dict(t, tpl=tpl, mask=(m > 0.99).astype(np.float32), k=t["k"] / 2)
        _TPL_CACHE[key] = t
    return _TPL_CACHE[key]


def panel_score(frame, r, layout):
    """名前欄 r のまわりに、対戦画面のパネルの枠が写っているか (見本との一致度 -1〜1)。見本が無ければ None。
    r は今の設定の範囲 (自動調整・手での調整・別の配置で、見本を作った範囲から位置や大きさが少しずれていてもよい)。"""
    from .config import PRESETS
    preset = PRESETS.get(layout) or {}
    ref = next((x for x in preset.get("regions", []) if x["id"] == r["id"]), None)
    t = _template_small(layout, r["id"])
    if not ref or not t or not preset.get("fit"):
        return None
    W, H = frame.size
    rw, rh = preset["fit"]["battle_ref_size"]
    s0 = (r["h"] * H) / (ref["h"] * rh)  # 見本を作った画面と比べた大きさ (の目安)
    th, tw = t["tpl"].shape[:2]
    best = -2.0
    for s in (s0 * 0.86, s0 * 0.93, s0, s0 * 1.07, s0 * 1.14):
        x = r["x"] * W - s * (ref["x"] * rw - t["box"][0])
        y = r["y"] * H - s * (ref["y"] * rh - t["box"][1])
        f = t["k"] / s  # 今の画面 → 見本の縮尺
        span = 0.35 * th / f  # 位置のずれの許容 (パネルの高さの 3〜4 割)
        box = (int(x - span), int(y - span), int(x + span + tw / f) + 1, int(y + span + th / f) + 1)
        crop = frame.crop(box)
        a = np.asarray(crop.resize((max(1, round(crop.width * f)), max(1, round(crop.height * f))),
                                   Image.BILINEAR), np.float32)
        best = max(best, _ncc_search(a, t["tpl"], t["mask"])[0])
    return best


def battle_hud_visible(frame, regions, layout, need=2, thr=0.65):
    """対戦画面の名前欄のパネルが need 個以上写っているか。見本が無い (判定できない) ときは None。
    実測: 対戦画面は 3〜4 個が 0.68 以上 (位置が少しずれた設定でも)、
    選出画面・メニュー・ボックスは全部 0.59 以下だった。"""
    hits, known = 0, False
    if frame.height > 600:  # 判定は小さい画像で十分 (毎フレーム使うので軽くする)
        frame = frame.resize((round(frame.width * 600 / frame.height), 600), Image.BILINEAR)
    for r in regions:
        sc = panel_score(frame, r, layout)
        if sc is None:
            continue
        known = True
        if sc >= thr:
            hits += 1
            if hits >= need:
                return True
    return False if known else None


def _name_like(text):
    t = text.strip()
    if any(w in t for w in UI_WORDS):
        return False
    digits = sum(ch.isdigit() for ch in t)
    return looks_like_name(t) and digits <= len(t) * 0.3 and not re.fullmatch(r"[\d\s:/%％.]+", t)


def fit_battle(frame, lines, regions_ref, ref_size, layout, read):
    """対戦画面の名前欄 (と顔画像・性別マーク) の範囲を合わせる。

    read(rect) → (種族 or None, スコア, 読めた文字列): 合わせた範囲で名前が読めるか確かめる関数
    戻り値: (新しい regions, メッセージのリスト, 合わせられた数)
    """
    W, H = frame.size
    rw, rh = ref_size
    out = [dict(r) for r in regions_ref]
    msgs, total, groups = [], 0, {}
    for side, label, area in (("opp", "相手", lambda l: _cy(l) < 0.3 and (l["x0"] + l["x1"]) / 2 > 0.45),
                              ("me", "自分", lambda l: _cy(l) > 0.6 and (l["x0"] + l["x1"]) / 2 < 0.55)):
        idx = sorted((i for i, r in enumerate(regions_ref) if r.get("side", "opp") == side),
                     key=lambda i: regions_ref[i]["x"])
        temps = {i: load_template(layout, regions_ref[i]["id"]) for i in idx}
        if not idx or not all(temps.values()):
            msgs.append(f"{label}の名前欄: 見本が無いため合わせられません")
            continue
        cand = [l for l in lines if area(l) and _name_like(l["text"])]
        if not cand:
            msgs.append(f"{label}の名前欄: 名前の文字が見つかりませんでした (対戦画面を表示してから押してください)")
            continue
        # 名前欄の行 (同じ高さに並ぶ文字) を選ぶ: 基準の高さに近い行
        exp_y = statistics.mean(regions_ref[i]["y"] + regions_ref[i]["h"] / 2 for i in idx)
        th = statistics.median(l["y1"] - l["y0"] for l in cand)
        row_y = min((_cy(l) for l in cand), key=lambda y: abs(y - exp_y))
        row = sorted([l for l in cand if abs(_cy(l) - row_y) < 0.6 * th], key=lambda l: l["x0"])
        # 大きさの予想: 文字の高さ (名前欄の高さの約 8 割)
        s0 = (statistics.median(l["y1"] - l["y0"] for l in row) * H) / (regions_ref[idx[0]]["h"] * rh * 0.8)
        # 各パネルの見本の左上の予想位置 (名前の文字の左端から逆算)。見つからない欄は隣の欄から
        guess = {}
        for n, l in enumerate(row[: len(idx)]):
            i = idx[n] if len(row) >= len(idx) else min(idx, key=lambda i: abs(regions_ref[i]["x"] - l["x0"]))
            r, t = regions_ref[i], temps[i]
            guess[i] = (l["x0"] * W - s0 * (r["x"] * rw - t["box"][0]), _cy(l) * H - s0 * (
                (r["y"] + r["h"] / 2) * rh - t["box"][1]))
        found = {}
        for i in sorted(guess, key=lambda i: regions_ref[i]["x"]):
            gx, gy = guess[i]
            c, s, x, y = match_panel(frame, temps[i], gx, gy, s0, span=0.6 * (regions_ref[i]["w"] * rw * s0))
            if c >= 0.45:
                found[i] = (c, s, x, y)
        for i in idx:  # 文字が見つからなかった欄: 見つかった欄から基準の間隔で予想して探す
            if i in found or not found:
                continue
            j = next(iter(found))
            _, s, x, y = found[j]
            gx = x + s * ((temps[i]["box"][0]) - temps[j]["box"][0])
            gy = y + s * ((temps[i]["box"][1]) - temps[j]["box"][1])
            c, s2, x2, y2 = match_panel(frame, temps[i], gx, gy, s, span=0.3 * regions_ref[i]["w"] * rw * s)
            found[i] = (c, s2, x2, y2) if c >= 0.45 else (0.0, s, gx, gy)
        if not found:
            msgs.append(f"{label}の名前欄: パネルが見つかりませんでした (基準の位置のまま)")
            continue
        groups[side] = (label, found, temps)

    # パネルはどれも同じ大きさのはず。大きさが他と違う (アイコン等が重なって別の所に合った) ものは、
    # 他のパネルと同じ大きさ・隣のパネルからの予想位置で探し直す
    allf = [f for _, fs, _ in groups.values() for f in fs.values()]
    if len(allf) >= 2 and max(allf)[0] >= 0.6:
        s_med = max(allf)[1]  # いちばんはっきり一致したパネルの大きさ
        for side, (label, found, temps) in groups.items():
            for i, (c, s, x, y) in list(found.items()):
                if abs(s / s_med - 1) <= 0.04 and c >= 0.7:
                    continue
                sib = [(j, f) for j, f in found.items() if j != i and abs(f[1] / s_med - 1) <= 0.04 and f[0] >= 0.7]
                if sib:
                    j, (_, sj, xj, yj) = sib[0]
                    gx = xj + s_med * (temps[i]["box"][0] - temps[j]["box"][0])
                    gy = yj + s_med * (temps[i]["box"][1] - temps[j]["box"][1])
                else:
                    gx, gy = x, y
                c2, s2, x2, y2 = match_panel(frame, temps[i], gx, gy, s_med,
                                             span=0.3 * regions_ref[i]["w"] * rw * s_med, spread=0.03)
                log.info("自動調整: %s を探し直し (大きさ %.2f→%.2f, 一致度 %.2f→%.2f)",
                         regions_ref[i]["id"], s, s2, c, c2)
                if c2 > c or (sib and c < 0.7) or abs(s / s_med - 1) > 0.04:
                    found[i] = (c2, s2, x2, y2)

    for side, (label, found, temps) in groups.items():
        names = []
        for i, (c, s, x, y) in found.items():
            r, t = regions_ref[i], temps[i]
            m = Mapper(ref_size, frame.size, s, t["box"], (x, y))
            n = dict(r)
            n.update(m.rect(r))
            for k in ("portrait", "gender"):
                if r.get(k):
                    n[k] = m.rect(r[k])
            if n.get("gender"):
                n["gender"] = refine_gender(frame, n["gender"])
            out[i] = n
            sp, sc, text = read(n)
            names.append(sp if sp and not sp.startswith("nick:") else (re.sub(r"^[ー一\-_/]+", "", nick_key(text)) or "?"))
            total += 1
        msgs.append(f"{label}の名前欄: {len(found)} 個合わせました ({'、'.join(names)})")
    corr = [c for _, fs, _ in groups.values() for c, _, _, _ in fs.values()]
    return out, msgs, total, (sum(corr) / len(corr) if corr else 0.0)  # 確からしさ: パネルの見本との一致度の平均


def refine_gender(frame, g):
    """性別マーク (赤丸・青丸) が見えていれば、範囲をその真ん中に合わせる。見えなければ (性別なし) そのまま。"""
    W, _ = frame.size
    hits = []
    for k in range(-16, 17):  # 機種によってマークの位置が少し違う (実測で幅の 2 倍弱) ので広めに
        dx = k * g["w"] * 0.15
        r = dict(g, x=g["x"] + dx)
        box = (int(r["x"] * W), int(r["y"] * frame.size[1]), int((r["x"] + r["w"]) * W),
               int((r["y"] + r["h"]) * frame.size[1]))
        if detect_gender(frame.crop(box), min_ratio=0.3):
            hits.append(dx)
    if not hits:
        return g
    # いちばん近いマーク (つながった範囲) の真ん中
    runs, cur = [], [hits[0]]
    for a, b in zip(hits, hits[1:]):
        if b - a > g["w"] * 0.2:
            runs.append(cur)
            cur = []
        cur.append(b)
    runs.append(cur)
    run = min(runs, key=lambda r: min(abs(v) for v in r))
    return dict(g, x=round(g["x"] + (run[0] + run[-1]) / 2, 4))


def autofit_frame(frame, layout, ocr, matcher, nicknames, scale):
    """今の画面が選出画面なら選出画面、そうでなければ対戦画面の範囲を、プリセット layout を基準に合わせる。
    戻り値: {"ok", "screen", "messages", "regions"?, "preview"?}"""
    from .config import PRESETS
    preset = PRESETS[layout]
    lines = detect_lines(ocr, frame)
    if find_marker_line(lines) is not None and preset.get("fit"):
        pv, msgs = fit_preview(frame, lines, preset)
        q = preview_quality(frame, pv) if pv else 0
        return {"ok": pv is not None, "screen": "選出画面", "messages": msgs, "preview": pv, "quality": q}

    def read(rect):
        W, H = frame.size
        box = (int(rect["x"] * W), int(rect["y"] * H), int((rect["x"] + rect["w"]) * W), int((rect["y"] + rect["h"]) * H))
        if box[0] < 0 or box[2] > W or box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return None, 0.0, ""
        texts = ocr.recognize(preprocess(frame.crop(box), scale))
        text = " / ".join(texts)
        best, score = None, 0.0
        for t in texts:
            sp, sc = matcher.match(t)
            if sp and sc > score:
                best, score = sp, sc
        if not best and nicknames is not None and texts and nicknames.match(text):
            return "nick:" + nick_key(text), 0.9, text
        return best, score, text

    regions, msgs, n, q = fit_battle(frame, lines, preset["regions"], preset["fit"]["battle_ref_size"],
                                     layout, read)
    return {"ok": n > 0, "screen": "対戦画面", "messages": msgs, "regions": regions if n else None, "quality": q}
