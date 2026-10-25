"""Streaming speech-to-text with Cartesia Ink turn detection (model "ink-2").

Not wired in yet: ally/voice.py still uses the batch POST /stt (cartesia_stt). Any failure here
raises a StreamError (a ToolError), so a caller can fall back to the batch call.

Protocol (docs.cartesia.ai, read 2026-10-24:
https://docs.cartesia.ai/use-the-api/stt/turns.md and
https://docs.cartesia.ai/api-reference/stt/turns/websocket.md):
- URL wss://api.cartesia.ai/stt/turns/websocket?model=ink-2&encoding=pcm_s16le&sample_rate=16000
  &cartesia_version=2026-08-14, plus optional turn_start_threshold, turn_eager_end_threshold,
  turn_end_threshold, turn_end_timeout_ms and repeatable keyterm.
- Auth: header X-API-Key (servers) or query access_token (short-lived, browsers). We use the header.
  The version goes in the cartesia_version query parameter; we also send a Cartesia-Version header.
- Client sends raw audio as binary frames (about 100 ms each), then {"type": "close"}. The server
  turns the buffered audio into events and closes the socket; keep reading until it does.
- Server sends JSON text: connected, turn.start, turn.update {transcript}, turn.eager_end
  {transcript}, turn.resume, turn.end {transcript}, error {status_code, title, message, error_code}.
  Every message has request_id. transcript is cumulative within a turn and never revised.
  The final text is the turn.end transcripts joined with spaces.

Unverified without a real key:
- how a bad key is refused (HTTP 401/403 on the upgrade, or an error message then close);
  both are handled and reported as StreamAuthError.
- whether "close" always ends an open turn with turn.end. If the socket closes mid-turn, the last
  turn.update text is kept (the docs say emitted text is final).
- which close code the server uses after "close" (any close frame after our close counts as normal).
- whether ink-2 accepts a language hint (the docs say it detects language; none is sent).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import urllib.parse
from typing import Callable, Iterable

from . import speech
from . import ws_client
from .registry import ToolError
from .speech import CARTESIA_VERSION

log = logging.getLogger("ally.stt_stream")

RATE = 16000
MODEL = "ink-2"
PATH = "/stt/turns/websocket"
TURN_EVENTS = ("turn.start", "turn.update", "turn.eager_end", "turn.resume", "turn.end")


class StreamError(ToolError):
    """Streaming speech-to-text failed. status is the HTTP/API status, 0 if unreachable or dropped."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class StreamAuthError(StreamError):
    """The API key is missing or was refused."""


class StreamTimeout(StreamError):
    """Cartesia didn't answer in time."""


class StreamClosed(StreamError):
    """The connection dropped or was closed before the stream finished."""


def stream_url(base: str = "", model: str = MODEL, sample_rate: int = RATE, encoding: str = "pcm_s16le",
               turn: dict | None = None, keyterms: Iterable[str] = ()) -> str:
    """The WebSocket URL. base is an http(s):// or ws(s):// root (default: speech.CARTESIA_URL)."""
    base = (base or speech.CARTESIA_URL).rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[8:]
    elif base.startswith("http://"):
        base = "ws://" + base[7:]
    params: list[tuple[str, str]] = [("model", model), ("encoding", encoding), ("sample_rate", str(int(sample_rate))),
                                     ("cartesia_version", CARTESIA_VERSION)]
    for k in ("start_threshold", "eager_end_threshold", "end_threshold", "end_timeout_ms"):
        if turn and turn.get(k) is not None:
            params.append(("turn_" + k, str(turn[k])))
    params += [("keyterm", t) for t in keyterms if t]
    return base + PATH + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


class StreamingSTT:
    """One Cartesia streaming session.

        stt = StreamingSTT(on_event=print)
        stt.open()
        for block in mic_blocks: stt.feed(block)      # 16 kHz mono int16 PCM
        text = stt.finish(timeout=10)                  # all final turns, joined
        stt.close()

    Events are dicts from the server ({"type": "turn.update", "transcript": ...}). They go to
    on_event (called on the reader thread; keep it quick) and to the .events queue.
    .partial is the current turn's text so far, .eager the last turn.eager_end text (cleared on
    turn.resume / turn.end), .finals the completed turns. Act on turn.end, not eager_end.
    """

    def __init__(self, on_event: Callable[[dict], None] | None = None, model: str = MODEL, sample_rate: int = RATE,
                 api_key: str | None = None, turn: dict | None = None, keyterms: Iterable[str] = (),
                 base_url: str = "", connect_timeout: float = 10.0):
        self.on_event = on_event
        self.model = model
        self.sample_rate = sample_rate
        self.api_key = api_key
        self.turn = turn
        self.keyterms = list(keyterms)
        self.base_url = base_url
        self.connect_timeout = connect_timeout
        self.events: queue.Queue[dict] = queue.Queue()
        self.partial = ""
        self.eager = ""
        self.finals: list[str] = []
        self.request_id = ""
        self.error: StreamError | None = None
        self._ws: ws_client.WSClient | None = None
        self._reader: threading.Thread | None = None
        self._done = threading.Event()
        self._turn_ended = threading.Condition()
        self._finishing = False
        self._closing = False

    # -- lifecycle
    def open(self) -> "StreamingSTT":
        key = self.api_key if self.api_key is not None else os.environ.get("CARTESIA_API_KEY", "").strip()
        if not key:
            raise StreamAuthError("Set CARTESIA_API_KEY in the .env file for voice input.", 401)
        url = stream_url(self.base_url, self.model, self.sample_rate, turn=self.turn, keyterms=self.keyterms)
        try:
            self._ws = ws_client.connect(url, {"X-API-Key": key, "Cartesia-Version": CARTESIA_VERSION},
                                         timeout=self.connect_timeout)
        except ws_client.WSHandshakeError as e:
            raise _handshake_error(e)
        except ws_client.WSError as e:
            raise StreamError(f"Can't reach Cartesia: {e}")
        self._reader = threading.Thread(target=self._read_loop, name="cartesia-stt", daemon=True)
        self._reader.start()
        return self

    def feed(self, pcm_block: bytes) -> None:
        """Send one block of audio. Raises StreamError if the stream has failed or closed."""
        if self.error:
            raise self.error
        if self._ws is None or self._finishing:
            raise StreamClosed("The speech stream isn't open.")
        if not pcm_block:
            return
        try:
            self._ws.send(bytes(pcm_block))
        except ws_client.WSError as e:
            raise self.error or StreamClosed(f"Lost the connection to Cartesia: {e}")

    def finish(self, timeout: float = 10.0) -> str:
        """Say the audio is over, wait for the last events, and return the final transcript."""
        if self._ws is None:
            raise StreamClosed("The speech stream isn't open.")
        if not self._finishing and not self._done.is_set():
            self._finishing = True
            try:
                self._ws.send(json.dumps({"type": "close"}))
            except ws_client.WSError:
                pass  # the reader records why
        if not self._done.wait(timeout):
            self._abort()
            raise StreamTimeout("Cartesia took too long to finish the transcript.")
        if self.error:
            raise self.error
        return self.transcript()

    def close(self) -> None:
        """Stop now, without waiting for more text."""
        self._abort()
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(timeout=2)

    def __enter__(self) -> "StreamingSTT":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- results
    def transcript(self) -> str:
        """Completed turns, plus the current turn's text if the stream ended inside it."""
        parts = list(self.finals)
        if self._done.is_set() and self.partial:
            parts.append(self.partial)
        return " ".join(p.strip() for p in parts if p.strip())

    def wait_turn(self, timeout: float | None = None) -> str | None:
        """Block until the next turn.end; its text, or None on timeout or end of stream.
        Raises if the stream failed."""
        with self._turn_ended:
            seen = len(self.finals)
            self._turn_ended.wait_for(lambda: len(self.finals) > seen or self._done.is_set(), timeout)
        if len(self.finals) > seen:
            return self.finals[seen]
        if self.error:
            raise self.error
        return None

    @property
    def done(self) -> bool:
        return self._done.is_set()

    # -- internals
    def _abort(self) -> None:
        self._closing = True
        if self._ws:
            self._ws.abort()

    def _read_loop(self) -> None:
        try:
            while True:
                try:
                    msg = self._ws.recv()
                except ws_client.WSClosed as e:
                    if not self._closing and not self.error and not (self._finishing and e.code is not None):
                        self.error = StreamClosed(f"Lost the connection to Cartesia ({e}).")
                    return
                except ws_client.WSError as e:
                    if not self._closing:
                        self.error = self.error or StreamError(f"Cartesia stream error: {e}")
                    return
                if isinstance(msg, bytes):
                    continue  # the API sends no binary messages
                try:
                    event = json.loads(msg)
                except ValueError:
                    log.debug("ignoring non-JSON message: %r", msg[:100])
                    continue
                if isinstance(event, dict):
                    self._on_message(event)
        finally:
            self._done.set()
            with self._turn_ended:
                self._turn_ended.notify_all()
            if self._ws:
                self._ws.abort()

    def _on_message(self, event: dict) -> None:
        kind = event.get("type", "")
        self.request_id = event.get("request_id") or self.request_id
        if kind == "error":
            status = int(event.get("status_code") or 0)
            text = event.get("message") or event.get("title") or "unknown error"
            cls = StreamAuthError if status in (401, 403) else StreamError
            self.error = self.error or cls(f"Cartesia speech-to-text error {status}: {text}", status)
            log.warning("cartesia stt error %s %s %s", status, event.get("error_code", ""), text)
        elif kind == "turn.start":
            self.partial, self.eager = "", ""
        elif kind == "turn.update":
            self.partial = event.get("transcript") or ""
        elif kind == "turn.eager_end":
            self.eager = event.get("transcript") or ""
        elif kind == "turn.resume":
            self.eager = ""
        elif kind == "turn.end":
            with self._turn_ended:
                self.finals.append((event.get("transcript") or "").strip())
                self.partial, self.eager = "", ""
                self._turn_ended.notify_all()
        self.events.put(event)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                log.exception("stt on_event callback failed")


def _handshake_error(e: ws_client.WSHandshakeError) -> StreamError:
    detail = (e.body or "").strip()[:200]
    if e.status in (401, 403):
        return StreamAuthError(f"Cartesia refused the API key ({e.status}). {detail}".strip(), e.status)
    if e.status:
        return StreamError(f"Cartesia speech-to-text error {e.status}: {detail or e}", e.status)
    return StreamError(f"Can't reach Cartesia: {e}")


def transcribe_blocks(blocks: Iterable[bytes], timeout: float = 30.0, **kwargs) -> str:
    """Stream PCM blocks (16 kHz mono int16) and return the final transcript.

    timeout covers the whole call. Raises StreamError (a ToolError) on any failure."""
    deadline = time.monotonic() + timeout
    stt = StreamingSTT(connect_timeout=min(10.0, timeout), **kwargs)
    stt.open()
    try:
        for block in blocks:
            if time.monotonic() > deadline:
                raise StreamTimeout("Cartesia took too long to finish the transcript.")
            stt.feed(block)
        return stt.finish(timeout=max(0.1, deadline - time.monotonic()))
    finally:
        stt.close()
