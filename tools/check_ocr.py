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


def main():
    from overlay.ocr import RapidOcr, WindowsOcr

    print("== Windows の文字認識 (Windows.Media.Ocr)")
    try:
        from winrt.windows.media.ocr import OcrEngine
        langs = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
        print("  使える言語:", ", ".join(langs) or "(なし)")
        text = WindowsOcr("ja").recognize(sample())
        print("  日本語: 使えます。テスト画像の読み取り結果:", text)
    except ImportError:
        print("  winrt パッケージが入っていません (setup.bat を実行してください)")
    except Exception as e:
        print("  日本語: 使えません ->", e)
        print("  README の「文字認識の確認」の手順で入れられます。入れなくても RapidOCR で動きます。")

    print("== RapidOCR")
    try:
        ocr = RapidOcr("cpu")
        print(f"  使えます ({ocr.name})。テスト画像の読み取り結果:", ocr.recognize(sample()))
    except Exception as e:
        print("  使えません ->", e)


if __name__ == "__main__":
    main()
