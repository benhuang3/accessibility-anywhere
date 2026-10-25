"""Send consented call screenshots in memory; no required imaging dependency."""

import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class ScreenShare:
    INTERVAL = 1.0
    MAX_SECONDS = 30 * 60
    TARGET_BYTES = 150_000

    def __init__(self, db_url, db_name, room_id, capture):
        self._url = db_url.rstrip("/")
        self._db = urllib.parse.quote(db_name, safe="")
        self._room = room_id
        self._capture = capture
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        try:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="risk-screen-share", daemon=True)
            self._thread.start()
        except Exception:
            self._stop.set()

    def stop(self):
        self._stop.set()

    def _post(self, path, args, token=""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self._url + path, data=json.dumps(args).encode(),
                                         headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read(65_536)

    def _frame(self):
        # screenshot_png is the same platform capture used by wire._make_guardian.
        try:
            from PIL import Image
        except ImportError:
            # Capture still works without Pillow, but cannot safely transcode PNG.
            return None
        raw = self._capture()
        if isinstance(raw, tuple):
            raw = raw[0]
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
        scale = min(1.0, 1280 / image.width, 65535 / image.height)
        if scale < 1:
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                                 Image.Resampling.LANCZOS)
        for _ in range(8):
            out = io.BytesIO()
            image.save(out, "JPEG", quality=60, optimize=True)
            jpeg = out.getvalue()
            if len(jpeg) <= self.TARGET_BYTES:
                return jpeg, image.width, image.height
            image = image.resize((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
                                 Image.Resampling.LANCZOS)
        return None

    @staticmethod
    def _rejection(error):
        # Inspect only our fixed protocol messages, never log the response body.
        with error:
            body = error.read(65_536).decode("utf-8", errors="replace")
        if "Screen sharing paused" in body:
            return "paused"
        if "This call is not available" in body:
            return "waiting"
        return "ended"

    def _run(self):
        # Fail closed and never log exception strings: they may contain capabilities.
        try:
            began = time.monotonic()
            deadline = began + self.MAX_SECONDS
            token = json.loads(self._post("/v1/identity", []))["token"]
            seq, opened = 0, False
            while not self._stop.is_set() and time.monotonic() < deadline:
                started = time.monotonic()
                try:
                    self._post(f"/v1/database/{self._db}/call/screen_share_ready", [self._room], token)
                    opened = True
                except urllib.error.HTTPError as error:
                    state = self._rejection(error)
                    if state == "ended" or (state == "waiting" and (opened or started - began >= 30)):
                        return
                    self._stop.wait(min(self.INTERVAL, max(0, deadline - time.monotonic())))
                    continue
                if self._stop.is_set() or time.monotonic() >= deadline:
                    return
                frame = self._frame()
                if frame is None:
                    return
                jpeg, width, height = frame
                if self._stop.is_set() or time.monotonic() >= deadline:
                    return
                try:
                    self._post(f"/v1/database/{self._db}/call/send_screen_frame",
                               [self._room, seq, list(jpeg), width, height], token)
                except urllib.error.HTTPError as error:
                    if self._rejection(error) != "paused":
                        return
                seq = (seq + 1) % (2 ** 32)
                self._stop.wait(min(max(0, self.INTERVAL - (time.monotonic() - started)),
                                    max(0, deadline - time.monotonic())))
        except Exception:
            pass
        finally:
            self._stop.set()
