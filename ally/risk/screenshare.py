"""Send consented call screenshots in memory; no required imaging dependency."""

import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.parse


class ScreenShare:
    MAX_FPS = 15
    INTERVAL = 1 / MAX_FPS
    SENDERS = 3
    MAX_SECONDS = 30 * 60
    TARGET_BYTES = 150_000
    RECOVERY_SECONDS = 5

    def __init__(self, db_url, db_name, room_id, capture):
        self._url = urllib.parse.urlsplit(db_url.rstrip("/"))
        self._db = urllib.parse.quote(db_name, safe="")
        self._room = room_id
        self._capture = capture
        self._stop = threading.Event()
        self._thread = None
        # Each sender owns one keep-alive connection; HTTPConnection is not thread safe.
        self._transport = threading.local()
        self._condition = threading.Condition()
        self._enabled = False
        self._capture_epoch = 0
        self._waiting = None
        self._quality = 60
        self._width = 1280
        self._interval = self.INTERVAL
        self._reduced_until = 0
        self._seq = 0
        self._pending = set()
        self._opened = False
        self._deadline = float("inf")

    @staticmethod
    def capture_screen(fallback):
        try:
            from PIL import ImageGrab
            return ImageGrab.grab(all_screens=False)
        except Exception:
            return fallback()

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
        connection = getattr(self._transport, "connection", None)
        self._transport.connection = None
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
                if getattr(self._transport, "connection", None) is None:
                    if self._url.scheme != "https":
                        raise ValueError("HTTPS required")
                    self._transport.connection = http.client.HTTPSConnection(self._url.hostname, self._url.port,
                                                                            timeout=5)
                self._transport.connection.request("POST", self._url.path + path, body=body, headers=headers)
                response = self._transport.connection.getresponse()
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
        if isinstance(raw, Image.Image):
            image = raw if raw.mode == "RGB" else raw.convert("RGB")
        else:
            with Image.open(io.BytesIO(raw)) as source:
                image = source.convert("RGB")
        with self._condition:
            if time.monotonic() >= self._reduced_until:
                self._quality, self._width = 60, 1280
            quality, width = self._quality, self._width
        scale = min(1.0, width / image.width, 65535 / image.height)
        if scale < 1:
            image.thumbnail((width, max(1, int(image.height * scale))), Image.Resampling.BILINEAR)
        for _ in range(8):
            out = io.BytesIO()
            image.save(out, "JPEG", quality=quality)
            jpeg = out.getvalue()
            if len(jpeg) <= self.TARGET_BYTES:
                return jpeg, image.width, image.height
            image = image.resize((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
                                 Image.Resampling.BILINEAR)
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
                        # Busy senders drop this capture; otherwise replace the latest slot.
                        self._waiting = frame if len(self._pending) < self.SENDERS else None
                        self._condition.notify_all()
                interval = max(1 / self.MAX_FPS, self._interval)
                self._stop.wait(min(max(0, interval - (time.monotonic() - started)),
                                    max(0, deadline - time.monotonic())))
        except Exception:
            pass
        finally:
            self.stop()

    def _step_down(self):
        with self._condition:
            self._quality, self._width = 45, 960
            self._reduced_until = time.monotonic() + self.RECOVERY_SECONDS

    def _send_frame(self, frame, token, epoch=None):
        jpeg, width, height = frame
        now = time.monotonic()
        with self._condition:
            if (self._stop.is_set() or now >= self._deadline
                    or (epoch is not None and (epoch != self._capture_epoch or not self._enabled))):
                return False
            if len(self._pending) >= self.SENDERS:
                return False
            seq = self._seq
            args = [self._room, seq, list(jpeg), width, height]
            self._seq += 1
            self._pending.add(seq)
        try:
            self._post(f"/v1/database/{self._db}/call/send_screen_frame", args, token)
            if time.monotonic() - now > 1:
                self._step_down()
            return True
        except urllib.error.HTTPError as error:
            error.close()
            self.stop()
            return False
        except Exception:
            self._step_down()
            return False
        finally:
            with self._condition:
                self._pending.discard(seq)

    def _ready(self, token, began, deadline):
        while not self._stop.is_set() and time.monotonic() < deadline:
            try:
                self._post(f"/v1/database/{self._db}/call/screen_share_ready", [self._room], token)
                self._opened = True
                self._set_enabled(True)
                return True
            except urllib.error.HTTPError as error:
                self._set_enabled(False)
                state = self._rejection(error)
                if state != "waiting" or self._opened or time.monotonic() - began >= 30:
                    self.stop()
                    return False
                self._stop.wait(min(1.0, max(0, deadline - time.monotonic())))
        return False

    def _consume(self, token, deadline):
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                with self._condition:
                    self._condition.wait_for(lambda: self._waiting is not None or self._stop.is_set(),
                                             timeout=min(0.25, max(0, deadline - time.monotonic())))
                    frame, self._waiting = self._waiting, None
                    epoch = self._capture_epoch
                    if (frame is None or not self._enabled or self._stop.is_set()
                            or time.monotonic() >= deadline):
                        continue
                self._send_frame(frame, token, epoch)
        except Exception:
            pass
        finally:
            self.stop()
            self._close()

    def _run(self):
        workers = []
        # Fail closed and never log exception strings: they may contain capabilities.
        try:
            began = time.monotonic()
            deadline = began + self.MAX_SECONDS
            self._deadline = deadline
            token = json.loads(self._post("/v1/identity", []))["token"]
            if not self._ready(token, began, deadline) or self._stop.is_set():
                return
            producer = threading.Thread(target=self._produce, args=(deadline,),
                                        name="risk-screen-capture", daemon=True)
            workers.append(producer)
            producer.start()
            for index in range(self.SENDERS - 1):
                sender = threading.Thread(target=self._consume, args=(token, deadline),
                                          name=f"risk-screen-send-{index}", daemon=True)
                workers.append(sender)
                sender.start()
            self._consume(token, deadline)
        except Exception:
            pass
        finally:
            self.stop()
            self._close()
            for worker in workers:
                try:
                    worker.join(timeout=0.5)
                except Exception:
                    pass
