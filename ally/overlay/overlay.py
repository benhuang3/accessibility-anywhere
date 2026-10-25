"""On-screen safety panel, help bubble, and highlight."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from typing import Any, Callable, Sequence

from .layout import (
    no_label,
    pause_left,
    HIGHLIGHT_COLOR,
    INFO_BG,
    INFO_FG,
    RISK_BG,
    RISK_BORDER,
    DisplayState,
    PanelState,
    answer_offer,
    button_labels,
    fit_card,
    highlight_bars,
    make_card_command,
    make_clear_command,
    make_highlight_command,
    make_offer_command,
    split_title,
)

log = logging.getLogger("ally")

START_TIMEOUT = 3.0
FONT_FAMILY = "Segoe UI"
BODY_PX = 26
TITLE_PX = 32
BUTTON_PX = 24
PRIMARY_BG = "#7A4100"


def _set_dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
_HWND_TOPMOST = -1
_GWL_EXSTYLE, _WS_EX_NOACTIVATE, _WS_EX_TOPMOST = -20, 0x08000000, 0x00000008


def _hwnd(win) -> int:
    return int(win.wm_frame(), 16)


def _no_activate(win) -> None:
    """Windows: showing or clicking this window must not pull focus away from the browser."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = _hwnd(win)
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST)
    except Exception:
        pass


def _show_without_focus(win) -> None:
    """Tk's deiconify activates the window. Show it, then hand focus straight back to whatever had it."""
    previous = 0
    if sys.platform == "win32":
        try:
            import ctypes

            previous = ctypes.windll.user32.GetForegroundWindow()
        except Exception:
            previous = 0
    _no_activate(win)
    win.deiconify()
    if previous:
        try:
            import ctypes

            win.update_idletasks()
            if ctypes.windll.user32.GetForegroundWindow() != previous:
                ctypes.windll.user32.SetForegroundWindow(previous)
        except Exception:
            pass


def _raise_without_focus(win) -> bool:
    """Put the window back on top without activating it. False when not on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.SetWindowPos(_hwnd(win), _HWND_TOPMOST, 0, 0, 0, 0,
                                                      _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE))
    except Exception:
        return False


class Overlay:
    def __init__(self, on_action: Callable[[str], None] | None = None):
        self.on_action = on_action
        self.error: str = ""
        self._q: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._state = DisplayState()
        self._panel = PanelState()
        self._card_key = None
        self._bubble_key = None
        self._focused_offer = None
        self._thread: threading.Thread | None = None
        self._root = None
        self._running = False
        self._offers: dict[int, Any] = {}
        self._pause_until: dict[int, float] = {}   # offer id -> when its 'Continue' unlocks
        self._extra_done: dict[int, str] = {}      # offer id -> confirmation shown after the extra action

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
            # Sizes below are for a 96-DPI screen; scale them so a 200% laptop screen gets the same physical size.
            try:
                self._scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
            except Exception:
                self._scale = 1.0
            self._tk = tk
            self._root = root
            self._hl_wins: list[Any] = []
            self._card_win = None
            self._card_bottom = 0
            self._offer_x = 0
            self._offer_win = None
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
                if not _raise_without_focus(win):
                    win.attributes("-topmost", True)
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
            current = self._panel.current_offer
            if current is not None:
                self._offers.pop(current.id, None)
            self._panel.clear()
            self._render()
            return
        if kind == "highlight":
            self._show_highlight(cmd[1])
            return
        if kind == "card":
            self._panel.card(cmd[1], cmd[2], cmd[3])
            self._render()
            return
        if kind == "extra_done":
            offer_id, note = cmd[1:]
            current = self._panel.current_offer
            offer = self._offers.get(offer_id)
            if current is None or current.id != offer_id or offer is None or offer.done:
                self._extra_done.pop(offer_id, None)
                return
            self._extra_done[offer_id] = note
            self._force_render()
            return
        if kind == "offer":
            offer = self._offers.get(cmd[1])
            if offer is not None and not getattr(offer, "done", False):
                current = self._panel.current_offer
                if current is not None and current.id != offer.id:
                    self._offers.pop(current.id, None)
                self._panel.offer(offer.id, getattr(offer, "context", ""), cmd[2])
                self._render()
            else:
                self._offers.pop(cmd[1], None)
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

    def _render(self) -> None:
        mode, card, offer = self._panel.view
        self._state.clear_card()
        if card is not None:
            self._state.note_card(card.kind)
        merged_offer = offer if mode == "panel" else None
        bubble = offer if mode in ("bubble", "card+bubble") else None
        card_key = (card, merged_offer) if card is not None else None

        # Reuse an early question's window when its warning arrives. There is
        # never a second risk window during the transition to a merged panel.
        if merged_offer is not None and self._offer_win is not None:
            if self._card_win is None:
                self._card_win, self._offer_win = self._offer_win, None
            else:
                self._close_offer()
            self._bubble_key = None
        if bubble is None:
            self._close_offer()
            self._bubble_key = None
        if card is None:
            self._close_card()
        elif card_key != self._card_key:
            self._card_win = self._build_panel(self._card_win, card, merged_offer)
        self._card_key = card_key

        if bubble is not None and bubble != self._bubble_key:
            self._offer_win = self._build_panel(self._offer_win, None, bubble)
            self._bubble_key = bubble
        self._park_offer_below_card()

    def _force_render(self) -> None:
        self._card_key = self._bubble_key = None
        self._render()

    def _build_panel(self, win, card, offer):
        tk = self._tk
        is_new = win is None
        had_focus = False
        if is_new:
            win = tk.Toplevel(self._root)
            win.withdraw()
            win.overrideredirect(True)
            win.attributes("-topmost", True)
        else:
            focused = win.focus_get()
            had_focus = focused is not None and focused.winfo_toplevel() == win
            for child in win.winfo_children():
                child.destroy()
        for key in ("<Return>", "<KP_Enter>", "<Escape>"):
            win.unbind(key)

        risk = card is not None and card.kind == "risk"
        bg = RISK_BG if risk else INFO_BG
        win.configure(bg=RISK_BORDER if risk else INFO_FG)
        surface = tk.Frame(win, bg=bg)
        surface.pack(fill="both", expand=True, padx=2, pady=2)
        if risk:
            tk.Frame(surface, bg=RISK_BORDER, width=self._px(10)).pack(side="left", fill="y")
        px = self._px
        inner = tk.Frame(surface, bg=bg)
        inner.pack(fill="both", expand=True, padx=px(32), pady=px(32))
        sw, sh = self._screen_size()
        x, y, width = fit_card(sw, sh)
        wrap = max(1, width - 4 - px(64) - (px(10) if risk else 0))

        def label(text, size, color=INFO_FG, weight="normal", pady=(0, 0), family=FONT_FAMILY):
            tk.Label(inner, text=text, wraplength=wrap,
                     font=(family, -px(size), weight), fg=color, bg=bg,
                     justify="left", anchor="w").pack(fill="x", pady=pady)

        if card is not None:
            if risk:
                label("Ally safety check", 18, "#6B4E16", pady=(0, px(12)))
            title, body = split_title(card.text)
            label(title, TITLE_PX, weight="bold")
            if body:
                label(body, BODY_PX, "#2B2B2B", pady=(px(12), 0))
        if offer is not None:
            label(offer.prompt, 28, pady=(px(16) if card else 0, 0), family="Segoe UI Semibold")
        row = tk.Frame(inner, bg=bg)
        row.pack(pady=(px(24), 0))
        first_btn = None
        if offer is not None:
            labels = button_labels(offer.prompt)
            real = self._offers.get(offer.id, offer)  # the panel state holds a copy; options live on the Offer
            pause = int(getattr(real, "pause_seconds", 0) or 0)
            if pause:
                self._pause_until.setdefault(offer.id, time.time() + pause)
            for index, (text, choice) in enumerate(zip(labels, ("yes", "no"))):
                btn = self._button(row, text, lambda c=choice, oid=offer.id: self._pick(oid, c),
                                   primary=index == 0, gap=24 if index == 0 else 0)
                if first_btn is None:
                    first_btn = btn
                if choice == "no":
                    self._no_btn, self._no_text = btn, text
            if pause:
                self._pause_tick(offer.id)
            for key, choice in (("<Return>", "yes"), ("<KP_Enter>", "yes"), ("<Escape>", "no")):
                win.bind(key, lambda _e, c=choice, oid=offer.id: self._pick(oid, c))
            extra = getattr(real, "extra_action", None)
            if extra:
                self._extra_row(inner, bg, offer.id, extra)
        else:
            for text in (("OK",) if risk else card.actions):
                btn = self._button(row, text, lambda lbl=text: self._card_action(lbl))
                if first_btn is None:
                    first_btn = btn

        win.update_idletasks()
        height = win.winfo_reqheight()
        if card is None and self._card_win is not None:
            y = self._card_bottom + 12
        self._place(win, x, y, width, height)
        if card is not None:
            self._card_bottom = y + height
        else:
            self._offer_x = x
        if is_new:
            _show_without_focus(win)
        # Never take keyboard focus from the app the person is using: on Windows that feels like being
        # alt-tabbed out. The panel is answered by click or by voice; Enter/Esc work once they click it.
        if first_btn is not None and had_focus:
            first_btn.focus_set()
        return win

    def _px(self, n: float) -> int:
        return int(round(n * getattr(self, "_scale", 1.0)))

    def _button(self, row, text: str, command, primary: bool = True, gap: int = 0):
        tk = self._tk
        px = self._px
        # Frames give buttons pixel dimensions; Tk Button.width uses characters.
        edge = tk.Frame(row, bg=PRIMARY_BG if primary else INFO_FG, width=px(260), height=px(64))
        edge.pack(side="left", padx=(0, px(gap)))
        edge.pack_propagate(False)
        bg, fg = (PRIMARY_BG, INFO_BG) if primary else (INFO_BG, INFO_FG)
        btn = tk.Button(edge, text=text, font=(FONT_FAMILY, -px(BUTTON_PX)),
                        bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                        relief="flat", borderwidth=0, highlightthickness=0,
                        command=command)
        btn.pack(fill="both", expand=True, padx=0 if primary else px(2), pady=0 if primary else px(2))
        return btn

    def _pause_tick(self, offer_id: int) -> None:
        """Count the 'Continue' button down; it stays locked until the pause is over."""
        current = self._panel.current_offer
        btn = getattr(self, "_no_btn", None)
        if current is None or current.id != offer_id or btn is None:
            return
        left = pause_left(self._pause_until.get(offer_id, 0), time.time())
        try:
            btn.configure(text=no_label(self._no_text, left), state="disabled" if left else "normal",
                          disabledforeground="#6B6B6B")
        except Exception:
            return
        if left and self._root is not None:
            self._root.after(1000, self._pause_tick, offer_id)

    def _extra_row(self, inner, bg, offer_id: int, extra) -> None:
        """An optional third choice that does not answer the question, e.g. 'Tell Priya'. It confirms in place."""
        tk, px = self._tk, self._px
        row = tk.Frame(inner, bg=bg)
        row.pack(pady=(px(16), 0))
        done = self._extra_done.get(offer_id)
        if done:
            tk.Label(row, text=done, font=(FONT_FAMILY, -px(22)), fg="#2E5E1E", bg=bg).pack()
            return
        label, callback = extra

        self._button(row, label, lambda: self._start_extra(offer_id, label, callback), primary=False)

    def _start_extra(self, offer_id: int, label: str, callback) -> None:
        current = self._panel.current_offer
        offer = self._offers.get(offer_id)
        if (offer_id in self._extra_done or current is None or current.id != offer_id
                or offer is None or offer.done):
            return
        target = label.partition(" ")[2] or label
        self._extra_done[offer_id] = f"Contacting {target}\N{HORIZONTAL ELLIPSIS}"
        self._force_render()

        def run():
            try:
                note = callback() or "Done."
            except Exception:
                log.exception("extra action failed")
                note = "That didn't work. Please try again."
            self._q.put(("extra_done", offer_id, str(note)[:120]))

        threading.Thread(target=run, name="overlay-contact", daemon=True).start()

    def _pick(self, offer_id: int, choice: str) -> str:
        current = self._panel.current_offer
        if current is None or current.id != offer_id:
            return "break"
        if choice == "no" and pause_left(self._pause_until.get(offer_id, 0), time.time()):
            return "break"  # the pause is the point: 'continue' only unlocks when it ends
        offer = self._offers.pop(offer_id, None)
        if offer is not None and answer_offer(offer, choice):
            self._panel.answer(offer_id)
            self._clear_highlight()
        else:
            self._panel.offer_done(offer_id)
        self._render()
        return "break"

    def _park_offer_below_card(self) -> None:
        """Non-risk questions stay immediately below a visible card."""
        if self._offer_win is None or self._card_win is None:
            return
        try:
            self._place(self._offer_win, self._offer_x, self._card_bottom + 12)
            if not _raise_without_focus(self._offer_win):
                self._offer_win.lift()
        except Exception:
            pass

    def _card_action(self, label: str) -> None:
        self._panel.ok()
        self._render()
        if self.on_action is not None:
            try:
                self.on_action(label)
            except Exception:
                log.exception("on_action failed")

    def _close_card(self) -> None:
        if self._card_win is not None:
            try:
                self._card_win.destroy()
            except Exception:
                pass
            self._card_win = None

    def _close_offer(self) -> None:
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
