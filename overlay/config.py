"""設定ファイル (config.json) の読み書き。無ければ既定値で作成する。"""
import copy
import json
import threading
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG = {
    "capture": {
        # obs: OBS WebSocket でソースのスクリーンショットを取得 (推奨)
        # screen: デスクトップ画面を直接キャプチャ / file: 画像ファイル (テスト用)
        "backend": "obs",
        # format: jpg は png よりエンコード/デコードが軽い (OBS 側の負荷も下がる)
        "obs": {"host": "localhost", "port": 4455, "password": "", "source": "映像キャプチャデバイス",
                "width": 1920, "height": 1080, "format": "jpg", "quality": 90},
        "screen": {"monitor": 1},
        "file": {"path": ""},
    },
    # backend: auto (Windows の文字認識 → 使えなければ RapidOCR) / windows / rapidocr (GPU 対応) / tesseract
    # device (rapidocr のみ): cpu / dml (DirectML: NVIDIA・AMD・Intel GPU) / cuda
    # threads (rapidocr のみ): CPU 実行時のスレッド数。1 が最も CPU 使用率が低い
    # scale を書くと OCR 前の拡大率を上書き (既定: windows・tesseract は 2、rapidocr は 1)
    "ocr": {"backend": "auto", "lang": "ja", "device": "dml", "threads": 1},
    "interval_sec": 0.5,
    "confirm_frames": 2,
    "min_ratio": 0.72,
    "server": {"host": "127.0.0.1", "port": 8765},
    # 採用率データの取得元。source: championsbattledata (性格・能力ポイントあり、毎日更新) / pikalytics
    # format: Doubles / Singles (championsbattledata)。pikalytics_format: Pikalytics のフォーマットコード
    # update_hour_utc: データ元の毎日の更新時刻 (UTC)。これより後に取得済みなら再取得しない。5 = 日本時間 14:00
    "usage": {"source": "championsbattledata", "format": "Doubles", "pikalytics_format": "gen9championsvgc2026regmc",
              "update_hour_utc": 5,
              "auto_update": True},  # 起動時に、新しいデータがあれば取得する
    # 選出画面の読み取り。マーカー (「4匹 選出してください」の行) に keywords が含まれていたら選出画面と判断し、
    # 相手のアイコン 6 つを登録済みアイコンと照合、自分のポケモン名 6 つを OCR する。
    # *_first / *_last は 1 匹目と 6 匹目の範囲 (間は等間隔で補間)。既定値はスマホ版の画面 (約 2.17:1) に合わせたもの
    "preview": {
        "enabled": True,
        "keywords": ["選出"],
        "count": 6,
        "icon_threshold": 0.8,   # アイコン一致度の閾値 (0〜1)
        "auto_my_party": True,   # 自分のパーティを選出画面から自動登録する
        # marker / opp_first / opp_last / my_first / ... の範囲は PRESETS から設定
    },
    # 表示設定 (設定画面から変更)。フォントは font (PC のフォント) / gfont (Google Fonts) / fontfile (web/fonts のファイル)
    "display": {"font": "", "gfont": "", "fontfile": "", "fontweight": 0},
    # 自分のパーティ (設定画面から登録)
    "my_party": [],
    # 対戦中のポケモン名の範囲 (画面に対する割合 0〜1)。既定はスマホ版。設定画面で Switch 版に切り替え / 微調整できる
    "layout": "phone",
    "regions": None,  # PRESETS から設定 (下の load_config 参照)
}


# 画面レイアウトごとの読み取り範囲 (割合座標)。*_first / *_last は選出画面の 1 匹目と 6 匹目 (間は補間)
# portrait: 名前の横の顔画像 (メガシンカ・ニックネームの判定) / gender: 性別マーク
def _r(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


PRESETS = {
    # スマホ版 (2556x1179 で計測。画面の端に余白がある機種。余白の無い機種用は regions_alt)
    "phone": {
        "label": "スマホ版",
        # 自動調整 (overlay/autofit.py) の基準: 計測した画面の大きさと、そこでの目印の位置
        "fit": {"ref_size": [2556, 1179], "battle_ref_size": [2556, 1179],
                "opp_panel": {"x0": 0.7567, "x1": 0.8838, "top0": 0.117, "pitch": 0.117},
                "my_name": {"x0": 0.1482, "cy0": 0.1476}, "marker": {"x1": 0.5347, "cy": 0.2384}},
        "regions": [
            {"id": "opp1", "label": "相手 左", "side": "opp", **_r(0.6385, 0.0483, 0.0822, 0.0399),
             "portrait": _r(0.5994, 0.0466, 0.0391, 0.0933), "gender": _r(0.7207, 0.0551, 0.0149, 0.0288)},
            {"id": "opp2", "label": "相手 右", "side": "opp", **_r(0.7958, 0.0483, 0.0951, 0.0399),
             "portrait": _r(0.757, 0.0466, 0.0391, 0.0933), "gender": _r(0.8912, 0.0551, 0.0149, 0.0288)},
            {"id": "me1", "label": "自分 左", "side": "me", **_r(0.133, 0.8473, 0.1033, 0.0399),
             "portrait": _r(0.09, 0.8431, 0.0423, 0.0925), "gender": _r(0.2371, 0.849, 0.0156, 0.0331)},
            {"id": "me2", "label": "自分 右", "side": "me", **_r(0.2899, 0.8473, 0.1037, 0.0399),
             "portrait": _r(0.2473, 0.8431, 0.0423, 0.0925), "gender": _r(0.4077, 0.849, 0.0156, 0.0331)},
        ],
        # 対戦画面の別の配置 (2316x1080 で計測。画面の端いっぱいまで使う機種)。名前が読めないとき自動で切り替える
        "regions_alt": [[
            {"id": "opp1", "label": "相手 左", "side": "opp", **_r(0.65, 0.045, 0.1075, 0.045),
             "portrait": _r(0.611, 0.0429, 0.04, 0.0944), "gender": _r(0.7565, 0.0515, 0.014, 0.03)},
            {"id": "opp2", "label": "相手 右", "side": "opp", **_r(0.8225, 0.045, 0.1075, 0.045),
             "portrait": _r(0.7825, 0.0429, 0.04, 0.0944), "gender": _r(0.928, 0.0515, 0.014, 0.03)},
            {"id": "me1", "label": "自分 左", "side": "me", **_r(0.0975, 0.8964, 0.105, 0.0429),
             "portrait": _r(0.054, 0.8964, 0.045, 0.0922), "gender": _r(0.202, 0.9018, 0.014, 0.03)},
            {"id": "me2", "label": "自分 右", "side": "me", **_r(0.27, 0.8964, 0.105, 0.0429),
             "portrait": _r(0.226, 0.8964, 0.045, 0.0922), "gender": _r(0.3745, 0.9018, 0.014, 0.03)},
        ]],
        "preview": {
            "marker": _r(0.385, 0.214, 0.1525, 0.048),
            "opp_first": _r(0.7725, 0.1246, 0.065, 0.0943), "opp_last": _r(0.7725, 0.7064, 0.065, 0.0943),
            "my_first": _r(0.145, 0.1268, 0.125, 0.0433), "my_last": _r(0.145, 0.7096, 0.125, 0.0433),
            "my_gender_first": _r(0.2745, 0.1333, 0.014, 0.0303), "my_gender_last": _r(0.2745, 0.7161, 0.014, 0.0303),
            "opp_gender_first": _r(0.84, 0.1831, 0.013, 0.0282), "opp_gender_last": _r(0.84, 0.766, 0.013, 0.0282),
            # 相手アイコンの右のタイプ (2 つ。1 タイプのポケモンは右側だけに出る)
            # 自分の名前の右のポケモンの絵 (ニックネームのポケモンを絵で判定する)
            "my_icon_first": _r(0.2975, 0.1213, 0.0525, 0.1029), "my_icon_last": _r(0.2975, 0.7042, 0.0525, 0.1029),
            "opp_type1_first": _r(0.8385, 0.130, 0.019, 0.0412), "opp_type1_last": _r(0.8385, 0.7129, 0.019, 0.0412),
            "opp_type2_first": _r(0.8605, 0.130, 0.019, 0.0412), "opp_type2_last": _r(0.8605, 0.7129, 0.019, 0.0412),
        },
    },
    # Switch 版 (1920x1080 で計測)
    "switch": {
        "label": "Switch 版",
        "fit": {"ref_size": [1920, 1080], "battle_ref_size": [1920, 1080],
                "opp_panel": {"x0": 0.8109, "x1": 0.9651, "top0": 0.1435, "pitch": 0.1167},
                "my_name": {"x0": 0.0736, "cy0": 0.1726}, "marker": {"x1": 0.542, "cy": 0.2634}},
        "regions": [
            {"id": "opp1", "label": "相手 左", "side": "opp", **_r(0.6224, 0.0481, 0.1198, 0.0398),
             "portrait": _r(0.5729, 0.0352, 0.0417, 0.1111), "gender": _r(0.7474, 0.0528, 0.0156, 0.0278)},
            {"id": "opp2", "label": "相手 右", "side": "opp", **_r(0.8292, 0.0481, 0.1198, 0.0398),
             "portrait": _r(0.7771, 0.0352, 0.0417, 0.1111), "gender": _r(0.9542, 0.0528, 0.0156, 0.0278)},
            {"id": "me1", "label": "自分 左", "side": "me", **_r(0.0833, 0.8639, 0.1198, 0.0389),
             "portrait": _r(0.0302, 0.8565, 0.0469, 0.1065), "gender": _r(0.2047, 0.8685, 0.0156, 0.0278)},
            {"id": "me2", "label": "自分 右", "side": "me", **_r(0.2896, 0.8639, 0.1198, 0.0389),
             "portrait": _r(0.2354, 0.8565, 0.0469, 0.1065), "gender": _r(0.4104, 0.8685, 0.0156, 0.0278)},
        ],
        "preview": {
            "marker": _r(0.3646, 0.2454, 0.1797, 0.0398),
            "opp_first": _r(0.8396, 0.1481, 0.0641, 0.0944), "opp_last": _r(0.8396, 0.7315, 0.0641, 0.0944),
            "my_first": _r(0.0729, 0.1556, 0.1484, 0.0352), "my_last": _r(0.0729, 0.7389, 0.1484, 0.0352),
            "my_gender_first": _r(0.2276, 0.1593, 0.0156, 0.0278), "my_gender_last": _r(0.2276, 0.7426, 0.0156, 0.0278),
            "opp_gender_first": _r(0.9135, 0.212, 0.0146, 0.0259), "opp_gender_last": _r(0.9135, 0.7954, 0.0146, 0.0259),
            "my_icon_first": _r(0.2578, 0.1435, 0.0609, 0.1083), "my_icon_last": _r(0.2578, 0.7269, 0.0609, 0.1083),
            "opp_type1_first": _r(0.9104, 0.1537, 0.024, 0.0426), "opp_type1_last": _r(0.9104, 0.737, 0.024, 0.0426),
            "opp_type2_first": _r(0.9365, 0.1537, 0.024, 0.0426), "opp_type2_last": _r(0.9365, 0.737, 0.024, 0.0426),
        },
    },
}


def apply_preset(cfg: dict, name: str) -> dict:
    """読み取り範囲をプリセットに置き換える (その他の設定はそのまま)。"""
    preset = PRESETS[name]
    cfg["layout"] = name
    cfg["regions"] = copy.deepcopy(preset["regions"])
    cfg.pop("regions_fitted", None)
    cfg.pop("preview_fitted", None)
    cfg.setdefault("preview", {}).update(copy.deepcopy(preset["preview"]))
    return cfg


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_lock = threading.Lock()


def _fill_layout(cfg: dict) -> dict:
    """範囲が未設定 (新規・古い設定) ならプリセットで埋める。"""
    name = cfg.get("layout") if cfg.get("layout") in PRESETS else "phone"
    preset = PRESETS[name]
    if not cfg.get("regions"):
        cfg["regions"] = copy.deepcopy(preset["regions"])
    for k, v in preset["preview"].items():
        cfg["preview"].setdefault(k, copy.deepcopy(v))
    return cfg


def load_config(path: Path = CONFIG_PATH) -> dict:
    with _lock:
        if not path.exists():
            cfg = _fill_layout(copy.deepcopy(DEFAULT_CONFIG))
            path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            return cfg
        return _fill_layout(_merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8-sig"))))


def save_config(cfg: dict, path: Path = CONFIG_PATH):
    with _lock:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
