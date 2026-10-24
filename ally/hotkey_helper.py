"""Separate process that listens for global hotkeys (pynput) and prints "HIT <n>" lines.

Running it apart from the agent means a crash in the keyboard hook can never take Ally down.
It exits by itself when the agent closes its stdin (so it never outlives the agent).
Usage: python -m ally.hotkey_helper '["<ctrl>+<alt>+r", ...]'
"""

from __future__ import annotations

import json
import os
import sys


def _out():
    return sys.stdout if sys.stdout is not None else os.fdopen(1, "w", buffering=1)


def main(argv: list[str]) -> int:
    out = _out()

    def emit(line: str) -> None:
        try:
            out.write(line + "\n")
            out.flush()
        except (OSError, ValueError):
            os._exit(0)

    try:
        combos = json.loads(argv[1]) if len(argv) > 1 else []
    except ValueError as e:
        emit(f"ERROR bad hotkey list: {e}")
        return 2
    try:
        from pynput import keyboard  # type: ignore
    except Exception as e:  # not installed, or no display (Linux without X11)
        emit(f"ERROR pynput unavailable: {type(e).__name__}: {e}")
        return 3
    mapping = {}
    for i, combo in enumerate(combos):
        mapping[combo] = (lambda i=i: emit(f"HIT {i}"))
    try:
        listener = keyboard.GlobalHotKeys(mapping)
    except ValueError as e:
        emit(f"ERROR invalid hotkey: {e}")
        return 4
    listener.start()
    emit("READY")
    try:
        while sys.stdin.readline():
            pass
    except (OSError, ValueError, KeyboardInterrupt):
        pass
    listener.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
