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
              "update_hour_utc": 5},
    # 選出画面の読み取り。マーカー (「4匹 選出してください」の行) に keywords が含まれていたら選出画面と判断し、
    # 相手のアイコン 6 つを登録済みアイコンと照合、自分のポケモン名 6 つを OCR する。
    # *_first / *_last は 1 匹目と 6 匹目の範囲 (間は等間隔で補間)。既定値はスマホ版の画面 (約 2.17:1) に合わせたもの
    "preview": {
        "enabled": True,
        "keywords": ["選出"],
        "marker": {"x": 0.385, "y": 0.214, "w": 0.1525, "h": 0.048},
        "opp_first": {"x": 0.7725, "y": 0.1246, "w": 0.065, "h": 0.0943},
        "opp_last": {"x": 0.7725, "y": 0.7064, "w": 0.065, "h": 0.0943},
        "my_first": {"x": 0.145, "y": 0.1268, "w": 0.125, "h": 0.0433},
        "my_last": {"x": 0.145, "y": 0.7096, "w": 0.125, "h": 0.0433},
        # 性別マーク (自分の名前の右 / 相手アイコンの右下)
        "my_gender_first": {"x": 0.2745, "y": 0.1333, "w": 0.014, "h": 0.0303},
        "my_gender_last": {"x": 0.2745, "y": 0.7161, "w": 0.014, "h": 0.0303},
        "opp_gender_first": {"x": 0.8400, "y": 0.1831, "w": 0.013, "h": 0.0282},
        "opp_gender_last": {"x": 0.8400, "y": 0.7660, "w": 0.013, "h": 0.0282},
        "count": 6,
        "icon_threshold": 0.8,   # アイコン一致度の閾値 (0〜1)
        "auto_my_party": True,   # 自分のパーティを選出画面から自動登録する
    },
    # 表示設定 (設定画面から変更)。フォントは font (PC のフォント) / gfont (Google Fonts) / fontfile (web/fonts のファイル)
    "display": {"font": "", "gfont": "", "fontfile": "", "fontweight": 0},
    # 自分のパーティ (設定画面から登録)
    "my_party": [],
    # 対戦中のポケモン名の範囲。座標は画面全体に対する割合 (0〜1)。設定画面 (/control) でドラッグして調整する
    # side: opp = 相手のポケモン名 / me = 自分のポケモン名 (場に出ているポケモンの強調表示に使う)
    # 既定値はスマホ版のダブルバトル画面 (約 2.14:1) に合わせたもの
    "regions": [
        # portrait: 名前の横の顔画像。メガシンカすると変わるので、その判定に使う
        # gender: 名前の右の性別マーク (♀/♂)。イエッサン等、性別でデータが違うポケモンの判定に使う
        {"id": "opp1", "label": "相手 左", "side": "opp", "x": 0.65, "y": 0.045, "w": 0.1075, "h": 0.045,
         "portrait": {"x": 0.611, "y": 0.0429, "w": 0.04, "h": 0.0944},
         "gender": {"x": 0.7565, "y": 0.0515, "w": 0.014, "h": 0.03}},
        {"id": "opp2", "label": "相手 右", "side": "opp", "x": 0.8225, "y": 0.045, "w": 0.1075, "h": 0.045,
         "portrait": {"x": 0.7825, "y": 0.0429, "w": 0.04, "h": 0.0944},
         "gender": {"x": 0.928, "y": 0.0515, "w": 0.014, "h": 0.03}},
        {"id": "me1", "label": "自分 左", "side": "me", "x": 0.0975, "y": 0.8964, "w": 0.105, "h": 0.0429,
         "portrait": {"x": 0.054, "y": 0.8964, "w": 0.045, "h": 0.0922},
         "gender": {"x": 0.202, "y": 0.9018, "w": 0.014, "h": 0.03}},
        {"id": "me2", "label": "自分 右", "side": "me", "x": 0.27, "y": 0.8964, "w": 0.105, "h": 0.0429,
         "portrait": {"x": 0.226, "y": 0.8964, "w": 0.045, "h": 0.0922},
         "gender": {"x": 0.3745, "y": 0.9018, "w": 0.014, "h": 0.03}},
    ],
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


_lock = threading.Lock()


def load_config(path: Path = CONFIG_PATH) -> dict:
    with _lock:
        if not path.exists():
            path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
            return copy.deepcopy(DEFAULT_CONFIG)
        return _merge(DEFAULT_CONFIG, json.loads(path.read_text(encoding="utf-8-sig")))


def save_config(cfg: dict, path: Path = CONFIG_PATH):
    with _lock:
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
