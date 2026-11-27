"""ポケモンチャンピオンズ 採用率オーバーレイ — 起動スクリプト

    python main.py

起動後:
  オーバーレイ (OBS ブラウザソースに追加): http://127.0.0.1:8765/overlay
  パーティ表示 (OBS ブラウザソースに追加): http://127.0.0.1:8765/party
  設定・調整画面 (普通のブラウザで開く):  http://127.0.0.1:8765/control
"""
import argparse
import logging
import shutil
import sys
import threading
from pathlib import Path

from overlay.config import load_config
from overlay.icons import IconDB
from overlay.recognizer import Recognizer, State
from overlay.server import App, serve
from overlay.usage import UsageDB


def update_usage_on_start(usage):
    """起動時に、データ元が前回の取得以降に更新されていれば採用率データを取得し、取得できたら差し替える。
    (取得中も前のデータで動く。取得できなくても前のデータのまま)"""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
    try:
        import update_usage
        if update_usage.update():
            usage.reload()
            logging.info("採用率データを更新しました (%d 匹)", len(usage.entries))
    except BaseException as e:  # SystemExit (取得できなかった) も含めて、起動中の処理は止めない
        logging.warning("採用率データを取得できませんでした。前のデータのまま使います (%s)", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-recognize", action="store_true", help="画面認識をせず手動切替のみで使う")
    ap.add_argument("--no-update", action="store_true", help="起動時に採用率データを取得しない")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cfg = load_config()
    usage = UsageDB()
    if not usage.entries:
        logging.warning("採用率データがありません。取得が終わるまでお待ちください (update_usage.bat でも取得できます)。")
    else:
        m = usage.meta
        logging.info("採用率データ: %s %s (%d 匹, 取得 %s)", m.get("format"), m.get("data_date"),
                     len(usage.entries), m.get("fetched_at"))

    old_types = Path(__file__).resolve().parent / "data" / "types"
    if old_types.is_dir():  # 以前の版で学習したタイプアイコン (今はデータ元のタイプアイコンで判定するので不要)
        shutil.rmtree(old_types, ignore_errors=True)
        logging.info("学習したタイプアイコン (data/types) を削除しました")
    icons = IconDB()
    n = icons.dedupe(lambda it: it["key"], 1)  # 選出画面のアイコンは 1 匹 1 枚 (以前の版で溜まった分を整理)
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

    if not args.no_update and cfg["usage"].get("auto_update", True):
        threading.Thread(target=update_usage_on_start, args=(usage,), daemon=True).start()

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
