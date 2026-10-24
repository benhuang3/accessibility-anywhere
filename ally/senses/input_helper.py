"""Separate process: mouse and keyboard activity for struggle detection. Prints one JSON object per line:

    {"ready": true}
    {"e": "c", "t": 1730000000.12, "x": 812, "y": 403}   a mouse button press
    {"e": "m", "t": ..., "x": ..., "y": ...}             pointer position (at most 10 per second)
    {"e": "k", "t": ...}                                 a key press (which key is never sent)
    {"e": "b", "t": ...}                                 a backspace or delete press
    {"error": "..."}

Privacy: it never records which characters are typed, only that a key or backspace was pressed.
Held keys that auto-repeat count once. Like the hotkey helper it exits when the agent closes its
stdin, so a crash in the input hooks can never take Ally down.
Usage: python -m ally.senses.input_helper
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time


def _out():
    return sys.stdout if sys.stdout is not None else os.fdopen(1, "w", buffering=1)


OUT = _out()
_lock = threading.Lock()


def emit(obj: dict) -> None:
    try:
        with _lock:
            OUT.write(json.dumps(obj) + "\n")
            OUT.flush()
    except (OSError, ValueError):
        os._exit(0)  # the agent is gone


def main() -> int:
    try:
        from pynput import keyboard, mouse  # type: ignore
    except Exception as e:  # not installed, or no display (Linux without X11)
        emit({"error": f"mouse and keyboard signals need pynput ({type(e).__name__}: {e})"[:300]})
        return 3

    erase = {keyboard.Key.backspace, keyboard.Key.delete}
    ignore = {getattr(keyboard.Key, n) for n in ("shift", "shift_r", "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l",
                                                 "alt_r", "alt_gr", "cmd", "cmd_r", "caps_lock") if hasattr(keyboard.Key, n)}
    held: set = set()
    last_move = [0.0]
    rest_generation = [0]

    def on_press(key):
        if key in held:  # auto-repeat while the key is held down
            return
        held.add(key)
        if key not in ignore:
            emit({"e": "b" if key in erase else "k", "t": round(time.time(), 3)})

    def on_release(key):
        held.discard(key)

    def on_click(x, y, _button, pressed):
        if pressed:
            emit({"e": "c", "t": round(time.time(), 3), "x": int(x), "y": int(y)})

    def on_move(x, y):
        now = time.time()
        if now - last_move[0] >= 0.1:
            last_move[0] = now
            emit({"e": "m", "t": round(now, 3), "x": int(x), "y": int(y)})
            rest_generation[0] += 1
            generation = rest_generation[0]
            def classify():
                time.sleep(1.0)
                if generation != rest_generation[0]:
                    return
                try:
                    from .clickable import is_clickable
                    value = is_clickable(int(x), int(y))
                except Exception:
                    value = None
                emit({"e": "r", "t": round(time.time(), 3), "x": int(x), "y": int(y), "clickable": value})
            threading.Thread(target=classify, daemon=True).start()

    try:
        ml = mouse.Listener(on_click=on_click, on_move=on_move)
        kl = keyboard.Listener(on_press=on_press, on_release=on_release)
        ml.start()
        kl.start()
    except Exception as e:
        emit({"error": f"couldn't watch the mouse and keyboard: {e}"[:300]})
        return 4
    emit({"ready": True})
    try:
        while sys.stdin.readline():
            pass
    except (OSError, ValueError, KeyboardInterrupt):
        pass
    ml.stop()
    kl.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
