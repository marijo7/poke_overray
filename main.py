"""ポケモンチャンピオンズ 採用率オーバーレイ — 起動スクリプト

    python main.py

起動後:
  オーバーレイ (OBS ブラウザソースに追加): http://127.0.0.1:8765/overlay
  パーティ表示 (OBS ブラウザソースに追加): http://127.0.0.1:8765/party
  設定・調整画面 (普通のブラウザで開く):  http://127.0.0.1:8765/control
"""
import argparse
import logging
import sys

from overlay.config import load_config
from overlay.icons import IconDB
from overlay.recognizer import Recognizer, State
from overlay.server import App, serve
from overlay.usage import UsageDB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-recognize", action="store_true", help="画面認識をせず手動切替のみで使う")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_config()
    usage = UsageDB()
    if not usage.entries:
        logging.warning("採用率データがありません。先に update_usage.bat (python tools/update_usage.py) を実行してください。")
    else:
        m = usage.meta
        logging.info("採用率データ: %s %s (%d 匹, 取得 %s)", m.get("format"), m.get("data_date"),
                     len(usage.entries), m.get("fetched_at"))

    icons = IconDB()
    n = icons.dedupe(lambda k: k, 3)  # 以前の版で溜まった、ほとんど同じアイコンを整理
    if n:
        logging.info("ほとんど同じアイコン %d 件を整理しました", n)
    logging.info("登録済みアイコン: %d 件", len(icons.items))
    state = State(usage, cfg.get("my_party"), icons)
    recognizer = None
    if not args.no_recognize:
        recognizer = Recognizer(state, lambda: cfg)
        recognizer.start()
    else:
        state.status.update(capture="無効 (--no-recognize)", ocr="-")

    app = App(cfg, state, usage, recognizer)
    httpd = serve(app)
    base = f"http://{cfg['server']['host']}:{cfg['server']['port']}"
    print(f"\n  オーバーレイ (OBS ブラウザソース): {base}/overlay")
    print(f"  パーティ表示 (OBS ブラウザソース): {base}/party")
    print(f"  設定・調整画面:                   {base}/control\n  終了は Ctrl+C\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if recognizer:
            recognizer.stop_event.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
