"""OCR バックエンド。Windows 標準 OCR / RapidOCR (GPU 対応) / Tesseract。"""
import asyncio

from PIL import Image, ImageOps


def preprocess(img: Image.Image, scale: float) -> Image.Image:
    if scale and scale != 1:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    return img


class WindowsOcr:
    """Windows 10/11 標準の OCR (Windows.Media.Ocr)。日本語の言語パックが必要。"""

    name = "Windows OCR"
    default_scale = 2.0

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
    default_scale = 2.0

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


class RapidOcr:
    """RapidOCR (PaddleOCR の日本語モデルを ONNX Runtime で実行)。

    device: cpu / dml (DirectML: NVIDIA・AMD・Intel の GPU) / cuda (NVIDIA, CUDA と cuDNN が必要)
    名前欄は 1 行なので文字領域検出は省き、文字認識モデルだけを動かす。
    """

    def __init__(self, device="cpu", threads=1):
        import numpy as np
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        self._np = np
        self.default_scale = 1.0  # 認識モデルが高さ 48px に縮小するので拡大は不要
        params = {
            "Global.use_det": False, "Global.use_cls": False, "Global.log_level": "warning",
            "Rec.lang_type": LangRec.JAPAN, "Rec.ocr_version": OCRVersion.PPOCRV4, "Rec.model_type": ModelType.MOBILE,
            # 既定 (全コア) だと待機中もスレッドが回り続け CPU 使用率が数倍になる。
            # 小さい画像を 0.5 秒おきに読む用途では 1 スレッドで十分速い
            "EngineConfig.onnxruntime.intra_op_num_threads": threads,
            "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        }
        if device == "dml":
            params["EngineConfig.onnxruntime.use_dml"] = True
        elif device == "cuda":
            params["EngineConfig.onnxruntime.use_cuda"] = True
        elif device != "cpu":
            raise ValueError(f"ocr.device は cpu / dml / cuda のいずれか: {device}")
        self.engine = RapidOCR(params=params)
        self.engine(np.zeros((48, 320, 3), np.uint8))  # モデル読み込み (初回はダウンロード)
        self.name = f"RapidOCR ({self._provider()})"

    def _provider(self):
        """実際に使われている実行環境。GPU を要求しても使えなければ CPU になる。"""
        try:
            providers = self.engine.text_rec.session.session.get_providers()
        except AttributeError:
            return "不明"
        if "DmlExecutionProvider" in providers:
            return "GPU: DirectML"
        if "CUDAExecutionProvider" in providers:
            return "GPU: CUDA"
        return "CPU"

    def recognize(self, img):
        out = self.engine(self._np.array(img.convert("RGB")))
        return [t for t in (out.txts or ()) if t]


def create_ocr(cfg: dict):
    backend = cfg.get("backend", "windows")
    if backend == "windows":
        return WindowsOcr(cfg.get("lang", "ja"))
    if backend == "rapidocr":
        return RapidOcr(cfg.get("device", "cpu"), cfg.get("threads", 1))
    if backend == "tesseract":
        return TesseractOcr(cfg.get("lang", "ja"), cfg.get("tesseract_cmd"))
    raise ValueError(f"未知の OCR バックエンド: {backend}")
