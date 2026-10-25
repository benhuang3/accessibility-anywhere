"""Send consented call screenshots in memory; no required imaging dependency."""

import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.parse
from collections import deque


class ScreenShare:
    INTERVAL = 0.25
    MAX_SECONDS = 30 * 60
    TARGET_BYTES = 150_000
    KEYFRAME_SECONDS = 5
    CHANGE_THRESHOLD = 1.5
    # Reserve 150 KB/s for each camera within the 1.2 MB/s call budget.
    SCREEN_BYTES_PER_SECOND = 900_000

    def __init__(self, db_url, db_name, room_id, capture):
        self._url = urllib.parse.urlsplit(db_url.rstrip("/"))
        self._db = urllib.parse.quote(db_name, safe="")
        self._room = room_id
        self._capture = capture
        self._stop = threading.Event()
        self._thread = None
        self._connection = None
        self._condition = threading.Condition()
        self._enabled = False
        self._capture_epoch = 0
        self._waiting = None
        self._quality = 60
        self._width = 1280
        self._interval = self.INTERVAL
        self._last_fingerprint = None
        self._last_sent = float("-inf")
        self._traffic = deque()
        self._seq = 0
        self._deadline = float("inf")

    def start(self):
        try:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="risk-screen-share", daemon=True)
            self._thread.start()
        except Exception:
            self.stop()

    def stop(self):
        self._stop.set()
        with self._condition:
            self._enabled = False
            self._waiting = None
            self._condition.notify_all()

    def _close(self):
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def _post(self, path, args, token=""):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = json.dumps(args).encode()
        for attempt in range(2):
            try:
                if self._stop.is_set() or time.monotonic() >= self._deadline:
                    raise OSError("Screen sharing stopped")
                if self._connection is None:
                    if self._url.scheme != "https":
                        raise ValueError("HTTPS required")
                    self._connection = http.client.HTTPSConnection(self._url.hostname, self._url.port, timeout=5)
                self._connection.request("POST", self._url.path + path, body=body, headers=headers)
                response = self._connection.getresponse()
                data = response.read(65_536)
                status = response.status
                if len(data) == 65_536:
                    self._close()
                if status >= 400:
                    raise urllib.error.HTTPError("", status, "Call rejected", {}, io.BytesIO(data))
                return data
            except (OSError, http.client.HTTPException) as error:
                # Protocol rejection is final; only a broken transport reconnects.
                self._close()
                if isinstance(error, urllib.error.HTTPError) or attempt or self._stop.is_set():
                    raise

    def _frame(self):
        try:
            from PIL import Image
        except ImportError:
            return None
        raw = self._capture()
        if isinstance(raw, tuple):
            raw = raw[0]
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
        fingerprint = image.resize((64, 36), Image.Resampling.BILINEAR).convert("L").tobytes()
        scale = min(1.0, self._width / image.width, 65535 / image.height)
        if scale < 1:
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))),
                                 Image.Resampling.LANCZOS)
        for _ in range(8):
            out = io.BytesIO()
            image.save(out, "JPEG", quality=self._quality, optimize=True)
            jpeg = out.getvalue()
            if len(jpeg) <= self.TARGET_BYTES:
                return jpeg, image.width, image.height, fingerprint
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

    def _set_enabled(self, enabled):
        with self._condition:
            self._enabled = enabled
            if not enabled:
                self._capture_epoch += 1
                self._waiting = None
            self._condition.notify_all()

    def _produce(self, deadline):
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                with self._condition:
                    self._condition.wait_for(lambda: self._enabled or self._stop.is_set(), timeout=0.25)
                    if not self._enabled or self._stop.is_set():
                        continue
                    epoch = self._capture_epoch
                started = time.monotonic()
                frame = self._frame()
                if frame is None:
                    return
                with self._condition:
                    if (self._enabled and epoch == self._capture_epoch and not self._stop.is_set()
                            and time.monotonic() < deadline):
                        # A single replaceable slot, never a queue of old screenshots.
                        self._waiting = frame
                        self._condition.notify_all()
                self._stop.wait(min(max(0, self._interval - (time.monotonic() - started)),
                                    max(0, deadline - time.monotonic())))
        except Exception:
            pass
        finally:
            self.stop()

    def _step_down(self):
        if self._quality > 45:
            self._quality = 45
        elif self._width > 960:
            self._width = 960
        else:
            self._interval = min(1.0, self._interval * 1.5)

    def _send_frame(self, frame, token):
        jpeg, width, height, fingerprint = frame
        now = time.monotonic()
        if self._last_fingerprint is not None and now - self._last_sent < self.KEYFRAME_SECONDS:
            difference = sum(abs(a - b) for a, b in zip(fingerprint, self._last_fingerprint)) / len(fingerprint)
            if difference < self.CHANGE_THRESHOLD:
                return False
        args = [self._room, self._seq, list(jpeg), width, height]
        self._post(f"/v1/database/{self._db}/call/send_screen_frame", args, token)
        finished = time.monotonic()
        self._seq = (self._seq + 1) % (2 ** 32)
        # Only an acknowledged send updates change detection and the keyframe clock.
        self._last_fingerprint, self._last_sent = fingerprint, finished
        self._traffic.append((finished, len(json.dumps(args).encode())))
        while self._traffic and self._traffic[0][0] <= finished - 10:
            self._traffic.popleft()
        if finished - now > 0.4 or sum(size for _, size in self._traffic) / 10 > self.SCREEN_BYTES_PER_SECOND:
            self._step_down()
        return True

    def _run(self):
        producer = None
        # Fail closed and never log exception strings: they may contain capabilities.
        try:
            began = time.monotonic()
            deadline = began + self.MAX_SECONDS
            self._deadline = deadline
            token = json.loads(self._post("/v1/identity", []))["token"]
            opened = False
            while not self._stop.is_set() and time.monotonic() < deadline:
                try:
                    self._post(f"/v1/database/{self._db}/call/screen_share_ready", [self._room], token)
                    opened = True
                except urllib.error.HTTPError as error:
                    self._set_enabled(False)
                    state = self._rejection(error)
                    if state == "ended" or (state == "waiting" and (opened or time.monotonic() - began >= 30)):
                        return
                    self._stop.wait(min(1.0, max(0, deadline - time.monotonic())))
                    continue
                if self._stop.is_set() or time.monotonic() >= deadline:
                    return
                self._set_enabled(True)
                if producer is None:
                    producer = threading.Thread(target=self._produce, args=(deadline,),
                                                name="risk-screen-capture", daemon=True)
                    producer.start()
                with self._condition:
                    self._condition.wait_for(lambda: self._waiting is not None or self._stop.is_set(),
                                             timeout=min(0.25, max(0, deadline - time.monotonic())))
                    frame, self._waiting = self._waiting, None
                if frame is None or self._stop.is_set() or time.monotonic() >= deadline:
                    continue
                try:
                    self._send_frame(frame, token)
                except urllib.error.HTTPError as error:
                    self._set_enabled(False)
                    if self._rejection(error) != "paused":
                        return
        except Exception:
            pass
        finally:
            self.stop()
            self._close()
            if producer is not None:
                try:
                    producer.join(timeout=0.5)
                except Exception:
                    pass
