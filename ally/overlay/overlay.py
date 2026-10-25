"""On-screen overlay: highlight, warning card, Yes/No offer bubble."""

from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import Any, Callable, Sequence

from .layout import (
    HIGHLIGHT_COLOR,
    INFO_BG,
    INFO_FG,
    RISK_BG,
    RISK_FG,
    RISK_BORDER,
    DisplayState,
    answer_offer,
    fit_card,
    highlight_bars,
    key_to_choice,
    make_card_command,
    make_clear_command,
    make_highlight_command,
    make_offer_command,
    sanitize_text,
)

log = logging.getLogger("ally")

START_TIMEOUT = 3.0
FONT_FAMILY = "Segoe UI"
BODY_PT = 28
TITLE_PT = 32
BUTTON_PT = 24


def _set_dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


class Overlay:
    def __init__(self, on_action: Callable[[str], None] | None = None):
        self.on_action = on_action
        self.error: str = ""
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._state = DisplayState()
        self._thread: threading.Thread | None = None
        self._root = None
        self._running = False
        self._offers: dict[int, Any] = {}

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return self._root is not None
        self.error = ""
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT):
            self.error = "no display"
            return False
        if self._root is None:
            if not self.error:
                self.error = "no display"
            return False
        self._running = True
        return True

    def stop(self) -> None:
        try:
            if self._running:
                self._q.put(("stop",))
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=2.0)
        except Exception:
            pass
        finally:
            self._running = False
            self._root = None

    def highlight(self, box) -> None:
        self._q.put(make_highlight_command(box))

    def show_card(self, text: str, actions: Sequence[str] = ("OK",), kind: str = "info") -> None:
        if not self._state.may_show_card(kind):
            return
        self._state.note_card(kind)
        self._q.put(make_card_command(text, actions, kind))

    def offer(self, offer) -> None:
        self._offers[offer.id] = offer
        self._q.put(make_offer_command(offer.id, offer.prompt))

    def clear(self) -> None:
        # The coordinator calls this after every offer; a warning card stays until the user dismisses it.
        self._q.put(make_clear_command())

    # ------------------------------------------------------------------ Tk thread
    def _run(self) -> None:
        try:
            import tkinter as tk

            _set_dpi_aware()
            root = tk.Tk()
            root.withdraw()
            self._tk = tk
            self._root = root
            self._hl_wins: list[Any] = []
            self._card_win = None
            self._card_bottom = 0
            self._offer_x = 0
            self._offer_win = None
            self._current_offer = None
            self._ready.set()
            root.after(20, self._poll)
            root.mainloop()
        except Exception as e:
            self.error = str(e) or "no display"
            log.debug("overlay start failed: %s", self.error)
            self._root = None
            self._ready.set()

    def _poll(self) -> None:
        try:
            while True:
                cmd = self._q.get_nowait()
                self._handle(cmd)
        except queue.Empty:
            pass
        except Exception:
            log.exception("overlay command failed")
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 25 == 0:
            self._keep_on_top()
        if self._root is not None:
            self._root.after(20, self._poll)

    def _keep_on_top(self) -> None:
        """A full-screen browser window can end up above our windows, which would hide a warning from the
        person it is for. Re-assert 'always on top' about twice a second while anything is showing."""
        for win in (self._card_win, self._offer_win, *self._hl_wins):
            if win is None:
                continue
            try:
                win.attributes("-topmost", True)
                win.lift()
            except Exception:
                pass

    def _handle(self, cmd: tuple[Any, ...]) -> None:
        kind = cmd[0]
        if kind == "stop":
            self._destroy_all()
            if self._root is not None:
                self._root.quit()
            return
        if kind == "clear":
            self._clear_highlight()
            self._close_offer()
            return
        if kind == "highlight":
            self._show_highlight(cmd[1])
            return
        if kind == "card":
            self._show_card(cmd[1], cmd[2], cmd[3])
            return
        if kind == "offer":
            offer = self._offers.get(cmd[1])
            if offer is not None:
                self._show_offer(offer)
            return

    @staticmethod
    def _place(win, x: int, y: int, w: int | None = None, h: int | None = None) -> None:
        """Size and position a borderless window. Tk can drop the position of a window that is created
        while another overlay window is up, so apply it, let Tk settle, and apply it again."""
        win.geometry(f"{w}x{h}+{x}+{y}" if w is not None else f"+{x}+{y}")
        win.update_idletasks()
        win.geometry(f"+{x}+{y}")

    def _screen_size(self) -> tuple[int, int]:
        return self._root.winfo_screenwidth(), self._root.winfo_screenheight()

    def _show_highlight(self, box) -> None:
        tk = self._tk
        self._clear_highlight()
        if not box:
            return
        for geom in highlight_bars(box):
            win = tk.Toplevel(self._root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg=HIGHLIGHT_COLOR)
            l, t, w, h = geom
            win.geometry(f"{w}x{h}+{l}+{t}")
            self._hl_wins.append(win)

    def _clear_highlight(self) -> None:
        for win in self._hl_wins:
            try:
                win.destroy()
            except Exception:
                pass
        self._hl_wins = []

    def _show_card(self, text: str, actions: Sequence[str], kind: str) -> None:
        tk = self._tk
        self._close_card()
        bg = RISK_BG if kind == "risk" else INFO_BG
        fg = RISK_FG if kind == "risk" else INFO_FG
        border = RISK_BORDER if kind == "risk" else INFO_FG
        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=border)
        inner = tk.Frame(win, bg=bg)
        inner.pack(fill="both", expand=True, padx=4, pady=4)

        sw, sh = self._screen_size()
        _, y, card_w = fit_card(sw, sh)
        body = sanitize_text(text)
        tk.Label(
            inner,
            text=body,
            wraplength=card_w - 8 - 48,
            font=(FONT_FAMILY, BODY_PT),
            fg=fg,
            bg=bg,
            justify="left",
            anchor="w",
        ).pack(padx=24, pady=(20, 16), anchor="w")

        btn_frame = tk.Frame(inner, bg=bg)
        btn_frame.pack(pady=(0, 20))
        first_btn = None
        for label in actions:
            btn = tk.Button(
                btn_frame,
                text=label,
                font=(FONT_FAMILY, BUTTON_PT),
                bg=INFO_FG if kind == "info" else RISK_BORDER,
                fg=INFO_BG if kind == "info" else RISK_BG,
                activebackground=INFO_FG,
                activeforeground=INFO_BG,
                relief="flat",
                width=12,
                height=2,
                command=lambda lbl=label: self._card_action(lbl),
            )
            btn.pack(pady=8)
            if first_btn is None:
                first_btn = btn

        win.update_idletasks()
        h = win.winfo_reqheight()
        x, _, w = fit_card(sw, sh)
        self._place(win, x, y, w, h)
        self._card_win = win
        self._card_bottom = y + h
        self._park_offer_below_card()
        if first_btn is not None:
            first_btn.focus_set()
            win.focus_force()

    def _park_offer_below_card(self) -> None:
        """The offer can arrive before its card; keep the question visible under the warning."""
        if self._offer_win is None or self._card_win is None:
            return
        try:
            self._place(self._offer_win, self._offer_x, self._card_bottom + 12)
            self._offer_win.lift()
        except Exception:
            pass

    def _card_action(self, label: str) -> None:
        if self.on_action is not None:
            try:
                self.on_action(label)
            except Exception:
                log.exception("on_action failed")
        self._close_card()
        self._state.clear_card()

    def _close_card(self) -> None:
        if self._card_win is not None:
            try:
                self._card_win.destroy()
            except Exception:
                pass
            self._card_win = None

    def _show_offer(self, offer) -> None:
        tk = self._tk
        self._close_offer()
        self._current_offer = offer
        bg = INFO_BG
        fg = INFO_FG
        win = tk.Toplevel(self._root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=RISK_BORDER)
        inner = tk.Frame(win, bg=bg)
        inner.pack(fill="both", expand=True, padx=4, pady=4)

        prompt = sanitize_text(offer.prompt)
        sw, sh = self._screen_size()
        _, y, card_w = fit_card(sw, sh)
        tk.Label(
            inner,
            text=prompt,
            wraplength=card_w - 8 - 48,
            font=(FONT_FAMILY, TITLE_PT),
            fg=fg,
            bg=bg,
            justify="left",
            anchor="w",
        ).pack(padx=24, pady=(20, 12), anchor="w")

        row = tk.Frame(inner, bg=bg)
        row.pack(padx=24, pady=(0, 20))

        def pick(choice: str) -> None:
            if self._current_offer is not None:
                answer_offer(self._current_offer, choice)
            self._close_offer()

        yes_btn = tk.Button(
            row,
            text="Yes",
            font=(FONT_FAMILY, BUTTON_PT),
            bg=RISK_BORDER,
            fg=RISK_BG,
            width=12,
            height=2,
            relief="flat",
            command=lambda: pick("yes"),
        )
        yes_btn.pack(side="left", padx=(0, 16))
        no_btn = tk.Button(
            row,
            text="No",
            font=(FONT_FAMILY, BUTTON_PT),
            bg=INFO_FG,
            fg=INFO_BG,
            width=12,
            height=2,
            relief="flat",
            command=lambda: pick("no"),
        )
        no_btn.pack(side="left")

        win.bind("<Return>", lambda _e: pick("yes"))
        win.bind("<KP_Enter>", lambda _e: pick("yes"))
        win.bind("<Escape>", lambda _e: pick("no"))

        win.update_idletasks()
        h = win.winfo_reqheight()
        x, y0, w = fit_card(sw, sh)
        if self._card_win is not None:
            y0 = self._card_bottom + 12
        self._place(win, x, y0, w, h)
        self._offer_x = x
        self._offer_win = win
        yes_btn.focus_set()
        win.focus_force()

    def _close_offer(self) -> None:
        self._current_offer = None
        if self._offer_win is not None:
            try:
                self._offer_win.destroy()
            except Exception:
                pass
            self._offer_win = None

    def _destroy_all(self) -> None:
        self._clear_highlight()
        self._close_offer()
        self._close_card()
