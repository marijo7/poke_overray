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
    # backend: windows (標準・CPU) / rapidocr (GPU 対応) / tesseract
    # device (rapidocr のみ): cpu / dml (DirectML: NVIDIA・AMD・Intel GPU) / cuda
    # threads (rapidocr のみ): CPU 実行時のスレッド数。1 が最も CPU 使用率が低い
    # scale を書くと OCR 前の拡大率を上書き (既定: windows・tesseract は 2、rapidocr は 1)
    "ocr": {"backend": "windows", "lang": "ja", "device": "dml", "threads": 1},
    "interval_sec": 0.5,
    "confirm_frames": 2,
    "min_ratio": 0.72,
    "server": {"host": "127.0.0.1", "port": 8765},
    "usage": {"format": "gen9championsvgc2026regmc"},
    # 座標は画面全体に対する割合 (0〜1)。設定画面 (/control) でドラッグして調整する
    "regions": [
        {"id": "opp1", "label": "相手 左", "x": 0.52, "y": 0.035, "w": 0.16, "h": 0.05},
        {"id": "opp2", "label": "相手 右", "x": 0.76, "y": 0.035, "w": 0.16, "h": 0.05},
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
