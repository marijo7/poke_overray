"""文字認識 (OCR) が使えるかを確認する。  python tools/check_ocr.py  (check_ocr.bat)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def sample():
    """「ガブリアス」と書いた画像 (読めるかの確認用)"""
    im = Image.new("RGB", (360, 70), (30, 30, 50))
    font = None
    for f in ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/YuGothB.ttc", "C:/Windows/Fonts/msgothic.ttc",
              "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"):
        try:
            font = ImageFont.truetype(f, 44)
            break
        except OSError:
            continue
    ImageDraw.Draw(im).text((12, 8), "ガブリアス", font=font, fill="white")
    return im


def report(ocr, label):
    """実際の認識と同じ前処理 (拡大) をして読み取り、ポケモン名の判定結果まで表示する。"""
    from overlay.names import NameMatcher
    from overlay.ocr import preprocess
    texts = ocr.recognize(preprocess(sample(), getattr(ocr, "default_scale", 2.0)))
    name, score = NameMatcher().match(" ".join(texts)) if texts else (None, 0)
    ok = "OK" if name == "ガブリアス" else "NG"
    print(f"  {label}: 読み取り {texts} → 判定 {name} ({score:.2f}) [{ok}]")


def main():
    from overlay.ocr import RapidOcr, WindowsOcr

    print("== Windows の文字認識 (Windows.Media.Ocr)")
    try:
        from winrt.windows.media.ocr import OcrEngine
        langs = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
        print("  使える言語:", ", ".join(langs) or "(なし)")
        report(WindowsOcr("ja"), "日本語: 使えます")
    except ImportError:
        print("  winrt パッケージが入っていません (setup.bat を実行してください)")
    except Exception as e:
        print("  日本語: 使えません ->", e)
        print("  README の「文字認識の確認」の手順で入れられます。入れなくても RapidOCR で動きます。")

    print("== RapidOCR")
    try:
        ocr = RapidOcr("cpu")
        report(ocr, f"使えます ({ocr.name})")
    except Exception as e:
        print("  使えません ->", e)


if __name__ == "__main__":
    main()
