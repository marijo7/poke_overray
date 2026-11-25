"""OCR バックエンド。Windows 標準 OCR (推奨) / Tesseract。"""
import asyncio

from PIL import Image, ImageOps


def preprocess(img: Image.Image, scale: float) -> Image.Image:
    if scale and scale != 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    return img


class WindowsOcr:
    """Windows 10/11 標準の OCR (Windows.Media.Ocr)。日本語の言語パックが必要。"""

    name = "Windows OCR"

    def __init__(self, lang="ja"):
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter

        self._SoftwareBitmap, self._DataWriter = SoftwareBitmap, DataWriter
        self._bgra8 = getattr(BitmapPixelFormat, "BGRA8", None) or getattr(BitmapPixelFormat, "Bgra8")
        self.engine = OcrEngine.try_create_from_language(Language(lang))
        if self.engine is None:
            raise RuntimeError(
                "Windows の日本語 OCR が見つかりません。設定 > 時刻と言語 > 言語 で「日本語」を追加し、"
                "言語オプションで「光学式文字認識」がインストールされているか確認してください。")
        self.loop = asyncio.new_event_loop()

    def recognize(self, img: Image.Image):
        r, g, b, a = img.convert("RGBA").split()
        raw = Image.merge("RGBA", (b, g, r, a)).tobytes()
        w = self._DataWriter()
        w.write_bytes(raw)
        bmp = self._SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), self._bgra8, img.width, img.height)
        result = self.loop.run_until_complete(self._recognize(bmp))
        return [line.text for line in result.lines]

    async def _recognize(self, bmp):
        return await self.engine.recognize_async(bmp)


class TesseractOcr:
    name = "Tesseract"

    def __init__(self, lang="ja", cmd=None):
        import pytesseract

        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self.pt = pytesseract
        self.lang = {"ja": "jpn"}.get(lang, lang)

    def recognize(self, img):
        # 白文字+縁取りのゲーム表示向けに、グレースケール化して両極性を試す
        gray = ImageOps.grayscale(img)
        texts = []
        for im in (gray, ImageOps.invert(gray)):
            t = self.pt.image_to_string(im, lang=self.lang, config="--psm 7").strip()
            if t:
                texts.append(t)
        return texts


def create_ocr(cfg: dict):
    backend = cfg.get("backend", "windows")
    if backend == "windows":
        return WindowsOcr(cfg.get("lang", "ja"))
    if backend == "tesseract":
        return TesseractOcr(cfg.get("lang", "ja"), cfg.get("tesseract_cmd"))
    raise ValueError(f"未知の OCR バックエンド: {backend}")
