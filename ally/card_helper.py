"""Separate process that shows Ally's reading card: big, clear text on top of every window.

Used when the words in a program can't be changed in place (Tier B of enlarge_text). Running it apart
from the agent keeps Tk on its own main thread and means a crash here can never take Ally down.
It exits by itself when the agent closes its stdin.

stdin, one JSON object per line:  {"op": "show", "text": "...", "box": [x, y, w, h] or null}
                                  {"op": "clear"}
stdout: READY, CLOSED (the user closed the card), ERROR <reason>
Usage: python -m ally.card_helper
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading

WIDTH_FRACTION = 0.6   # of the screen width
MAX_WIDTH = 1100       # physical pixels
MARGIN = 24
FONT_POINTS = 30       # points scale with the display's DPI; pixels would shrink at 200%
BG, FG, EDGE = "#FFFDE7", "#111111", "#1A237E"


def card_position(box, screen_w: int, screen_h: int, card_w: int, card_h: int) -> tuple[int, int]:
    """Top-left of the card: just below `box` (or above it if there's no room), centered on it
    horizontally, kept on screen. With no box, the middle of the screen."""
    if not box:
        return max(0, (screen_w - card_w) // 2), max(0, (screen_h - card_h) // 2)
    x, y, w, h = (int(v) for v in box)
    left = x + w // 2 - card_w // 2
    top = y + h + MARGIN
    if top + card_h > screen_h - MARGIN:
        top = y - card_h - MARGIN
    if top < MARGIN:  # no room above or below: cover the box rather than go off screen
        top = (screen_h - card_h) // 2
    left = max(MARGIN, min(left, screen_w - card_w - MARGIN))
    top = max(0, min(top, screen_h - card_h))
    return left, top


def _out():
    return sys.stdout if sys.stdout is not None else os.fdopen(1, "w", buffering=1)


def _dpi_aware() -> None:
    """Windows: real pixels, the same ones UI Automation boxes use (see platforms/windows.py)."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    out = _out()

    def emit(line: str) -> None:
        try:
            out.write(line + "\n")
            out.flush()
        except (OSError, ValueError):
            os._exit(0)

    _dpi_aware()
    try:
        import tkinter as tk
    except Exception as e:
        emit(f"ERROR tkinter unavailable: {type(e).__name__}: {e}")
        return 3
    try:
        root = tk.Tk()
    except Exception as e:  # no display
        emit(f"ERROR can't open a window: {type(e).__name__}: {e}")
        return 4
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=EDGE)
    inner = tk.Frame(root, bg=BG, padx=28, pady=22)
    inner.pack(padx=4, pady=4, fill="both", expand=True)
    label = tk.Label(inner, text="", bg=BG, fg=FG, justify="left", anchor="w",
                     font=("Segoe UI" if sys.platform == "win32" else "Helvetica", FONT_POINTS))
    label.pack(fill="both", expand=True)

    def hide(by_user: bool = False) -> None:
        root.withdraw()
        if by_user:
            emit("CLOSED")

    close = tk.Button(inner, text="Close", command=lambda: hide(True), font=(None, 18, "bold"),
                      padx=24, pady=10, bg=EDGE, fg="white", activebackground=EDGE, relief="flat")
    close.pack(anchor="e", pady=(18, 0))
    root.bind("<Escape>", lambda _e: hide(True))

    def show(text: str, box) -> None:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(MAX_WIDTH, int(sw * WIDTH_FRACTION))
        label.configure(text=text, wraplength=width - 70)
        root.update_idletasks()
        w, h = max(width, root.winfo_reqwidth()), root.winfo_reqheight()
        x, y = card_position(box, sw, sh, w, h)
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.deiconify()
        root.lift()
        root.attributes("-topmost", True)

    jobs: queue.Queue = queue.Queue()

    def read_stdin() -> None:
        try:
            for line in sys.stdin:
                try:
                    jobs.put(json.loads(line))
                except ValueError:
                    emit("ERROR bad command")
        except (OSError, ValueError):
            pass
        jobs.put(None)

    def pump() -> None:
        try:
            while True:
                job = jobs.get_nowait()
                if job is None:
                    root.destroy()
                    return
                if job.get("op") == "show":
                    show(str(job.get("text", ""))[:4000], job.get("box"))
                elif job.get("op") == "clear":
                    hide()
        except queue.Empty:
            pass
        root.after(50, pump)

    threading.Thread(target=read_stdin, daemon=True, name="card-stdin").start()
    root.after(50, pump)
    emit("READY")
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
