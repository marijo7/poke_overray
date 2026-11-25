"""画面の取得。OBS WebSocket / デスクトップ / 画像ファイル。"""
import base64
import io

from PIL import Image


class OBSCapture:
    """OBS WebSocket (v5, OBS 28 以降標準) で指定ソースのスクリーンショットを取る。"""

    def __init__(self, host, port, password, source, width=1920, height=1080, **_):
        import obsws_python as obs  # 遅延 import

        self._obs = obs
        self.conn = dict(host=host, port=port, password=password, timeout=5)
        self.source, self.width, self.height = source, width, height
        self.client = None

    def _connect(self):
        self.client = self._obs.ReqClient(**self.conn)

    def grab(self) -> Image.Image:
        if self.client is None:
            self._connect()
        try:
            resp = self.client.get_source_screenshot(self.source, "png", self.width, self.height, -1)
        except Exception:
            self.client = None  # 次回再接続
            raise
        data = resp.image_data.split(",", 1)[1]
        return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")

    def describe(self):
        return f"OBS ソース「{self.source}」"


class ScreenCapture:
    def __init__(self, monitor=1, **_):
        import mss

        self.sct = None
        self._mss = mss
        self.monitor = monitor

    def grab(self):
        if self.sct is None:  # mss はスレッドごとに生成する必要がある
            self.sct = self._mss.mss()
        shot = self.sct.grab(self.sct.monitors[self.monitor])
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def describe(self):
        return f"モニター {self.monitor}"


class FileCapture:
    def __init__(self, path, **_):
        self.path = path

    def grab(self):
        return Image.open(self.path).convert("RGB")

    def describe(self):
        return f"ファイル {self.path}"


def create_capture(cfg: dict):
    backend = cfg["backend"]
    cls = {"obs": OBSCapture, "screen": ScreenCapture, "file": FileCapture}[backend]
    return cls(**cfg.get(backend, {}))
