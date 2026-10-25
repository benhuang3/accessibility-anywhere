"""On-screen companion: Ally's bear in the corner of the screen, its speech card, and the highlight."""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .layout import (
    no_label,
    pause_left,
    BADGE_BG,
    BADGE_FG,
    BADGE_ICON,
    BEAR_GAP,
    CARD_BG,
    CARD_EDGE,
    CARD_PAD,
    CARD_WIDTH,
    DISABLED_BG,
    DISABLED_EDGE,
    DISABLED_FG,
    DIVIDER,
    EDGE_MARGIN,
    GOOD_FG,
    HIGHLIGHT_COLOR,
    KEY_COLOR,
    LINK_PRIMARY,
    PRIMARY_BG,
    PRIMARY_EDGE,
    PRIMARY_FG,
    PRIMARY_HOVER,
    QUIET_BG,
    QUIET_EDGE,
    QUIET_FG,
    QUIET_HOVER,
    STACK_GAP,
    TEXT_FG,
    TITLE_FG,
    TOAST_SECONDS,
    DisplayState,
    PanelState,
    answer_offer,
    bear_width,
    button_labels,
    button_text,
    card_tone,
    corner_origin,
    highlight_bars,
    make_card_command,
    make_clear_command,
    make_highlight_command,
    make_offer_command,
    rounded_rect,
    split_title,
)

log = logging.getLogger("ally")

START_TIMEOUT = 3.0
FONT_FAMILY = "Segoe UI"
SEMIBOLD = "Segoe UI Semibold"
TITLE_PX = 26
BODY_PX = 19
BUTTON_PX = 18
BUTTON_H = 64
BEAR_DIR = Path(__file__).with_name("bear")
# Segoe Fluent Icons / Segoe MDL2 Assets code points.
GLYPHS = {"shield": "", "question": "", "check": "", "phone": ""}


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
_SPI_GETWORKAREA = 0x0030


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


def _windows_work_area(reserved=None) -> tuple[int, int, int, int] | None:
    """The screen minus the taskbar, in physical pixels."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        if reserved is not None:
            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                            ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
            user32.MonitorFromRect.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
            user32.GetMonitorInfoW.restype = wintypes.BOOL
            call_rect = wintypes.RECT(*reserved)
            monitor = user32.MonitorFromRect(ctypes.byref(call_rect), 2)  # nearest monitor
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = info.work
                return rect.left, rect.top, rect.right, rect.bottom
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return None


def _int(value, default: int) -> int:
    """Tk sizes as ints; tests drive the overlay with mocked widgets that return mocks."""
    return value if isinstance(value, int) else default


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
        self._reserved = None
        self._thread: threading.Thread | None = None
        self._root = None
        self._running = False
        self._offers: dict[int, Any] = {}
        self._pause_until: dict[int, float] = {}   # offer id -> when its 'Continue' unlocks
        self._extra_done: dict[int, str] = {}      # offer id -> confirmation shown after the extra action
        self._card_box = (0, 0)                    # left and top of the visible card, for stacking a question above it
        self._offer_has_bear = False
        self._images: dict[str, Any] = {}
        self._glyph_font: str | None = None

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

    def set_reserved(self, rect) -> None:
        self._q.put(("reserved", rect))

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
            import tkinter.font as tkfont

            _set_dpi_aware()
            root = tk.Tk()
            root.withdraw()
            # Sizes below are for a 96-DPI screen; scale them so a 200% laptop screen gets the same physical size.
            try:
                self._scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
            except Exception:
                self._scale = 1.0
            try:
                families = set(tkfont.families(root))
                self._glyph_font = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in families), None)
            except Exception:
                self._glyph_font = None
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
        if kind == "reserved":
            if self._reserved != cmd[1]:
                self._reserved = cmd[1]
                self._force_render()
            return
        if kind == "clear":
            self._clear_highlight()
            current = self._panel.current_offer
            if current is not None:
                offer = self._offers.pop(current.id, None)
                if offer is not None and offer.choice == "dismissed" and offer.answered_by == "call":
                    self._panel.answer(current.id)
                    self._state.clear_card()
                self._extra_done.pop(current.id, None)
            self._panel.clear()
            self._render()
            return
        if kind == "highlight":
            self._show_highlight(cmd[1])
            return
        if kind == "card":
            current = self._panel.current_offer
            offer = self._offers.get(current.id) if current is not None else None
            if cmd[3] == "done" and offer is not None and offer.done:
                self._offers.pop(current.id, None)
                self._panel.offer_done(current.id)
                self._clear_highlight()
            self._panel.card(cmd[1], cmd[2], cmd[3])
            self._render()
            return
        if kind == "extra_done":
            offer_id, note = cmd[1:]
            current = self._panel.current_offer
            offer = self._offers.get(offer_id)
            if current is None or current.id != offer_id or offer is None or offer.done:
                self._extra_done.pop(offer_id, None)
                if (current is not None and current.id == offer_id and offer is not None
                        and offer.choice == "dismissed" and offer.answered_by == "call"):
                    self._panel.answer(offer_id)
                    self._offers.pop(offer_id, None)
                    self._state.clear_card()
                    self._clear_highlight()
                    self._render()
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

    def _work_area(self) -> tuple[int, int, int, int]:
        area = _windows_work_area(self._reserved)
        if area is not None:
            return area
        sw, sh = self._screen_size()
        return 0, 0, sw, sh - self._px(48)  # leave room for a taskbar or dock

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
            if self._card_win is not None:
                self._bubble_key = None  # the question moves down into the corner and takes the bear
            self._close_card()
        elif card_key != self._card_key:
            self._card_win = self._build_panel(self._card_win, card, merged_offer)
        self._card_key = card_key

        # Only the window in the corner carries the bear: a question above a card gives it up.
        if bubble is not None and self._card_win is not None and self._offer_has_bear:
            self._bubble_key = None
        if bubble is not None and bubble != self._bubble_key:
            self._offer_win = self._build_panel(self._offer_win, None, bubble)
            self._bubble_key = bubble
        self._park_offer_above_card()

    def _force_render(self) -> None:
        self._card_key = self._bubble_key = None
        self._render()

    # ------------------------------------------------------------------ drawing
    def _px(self, n: float) -> int:
        return int(round(n * getattr(self, "_scale", 1.0)))

    def _bear_image(self, name: str):
        width = bear_width(getattr(self, "_scale", 1.0))
        key = f"{name}-{width}"
        if key not in self._images:
            path = BEAR_DIR / f"{key}.png"
            try:
                self._images[key] = self._tk.PhotoImage(master=self._root, file=str(path)) if path.exists() else None
            except Exception:
                log.debug("bear sprite failed: %s", path)
                self._images[key] = None
        return self._images[key]

    def _text_width(self, text: str, font) -> int:
        try:
            import tkinter.font as tkfont

            return int(tkfont.Font(root=self._root, font=font).measure(text))
        except Exception:
            return int(len(text) * abs(font[1]) * 0.6)

    def _glyph(self, parent, name: str, color: str, size: int, bg: str = CARD_BG):
        """A small icon from the Windows symbol font, or nothing when that font is missing."""
        if not self._glyph_font:
            return None
        lbl = self._tk.Label(parent, text=GLYPHS[name], font=(self._glyph_font, -self._px(size)),
                             fg=color, bg=bg, bd=0, padx=0, pady=0)
        return lbl

    def _build_panel(self, win, card, offer):
        tk = self._tk
        px = self._px
        is_new = win is None
        had_focus = False
        if is_new:
            win = tk.Toplevel(self._root)
            win.withdraw()
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-transparentcolor", KEY_COLOR)  # Windows: the canvas around the card shows through
            except Exception:
                pass
        else:
            focused = win.focus_get()
            had_focus = focused is not None and focused.winfo_toplevel() == win
            for child in win.winfo_children():
                child.destroy()
        for key in ("<Return>", "<KP_Enter>", "<Escape>"):
            win.unbind(key)
        win.configure(bg=KEY_COLOR)

        tone = card_tone(card, offer)
        with_bear = card is not None or self._card_win is None
        card_w = px(CARD_WIDTH)
        if tone.toast:  # a short note hugs its text
            text_w = self._text_width(card.text, (SEMIBOLD, -px(BODY_PX)))
            card_w = min(card_w, 2 * px(CARD_PAD) + px(34) + text_w + px(4))
        inner_w = card_w - 2 * px(CARD_PAD)
        canvas = tk.Canvas(win, bg=KEY_COLOR, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        inner = tk.Frame(canvas, bg=CARD_BG)

        def label(parent, text, size, color, family=FONT_FAMILY, weight="normal", pady=(0, 0)):
            lbl = tk.Label(parent, text=text, wraplength=inner_w, font=(family, -px(size), weight),
                           fg=color, bg=CARD_BG, justify="left", anchor="w", bd=0, padx=0, pady=0)
            lbl.pack(fill="x", pady=pady)
            return lbl

        first_btn = None
        footer = False
        if tone.toast:
            row = tk.Frame(inner, bg=CARD_BG)
            row.pack(fill="x")
            icon = self._glyph(row, "check", GOOD_FG, 22)
            if icon is not None:
                icon.pack(side="left", padx=(0, px(12)))
            tk.Label(row, text=card.text, font=(SEMIBOLD, -px(BODY_PX)), fg=TITLE_FG, bg=CARD_BG,
                     wraplength=inner_w - px(34), justify="left", anchor="w").pack(side="left", fill="x")
            if self._root is not None:
                self._root.after(int(TOAST_SECONDS * 1000), self._dismiss_toast, card)
        else:
            if tone.badge:
                self._badge(inner, tone.badge, tone.icon).pack(anchor="w", pady=(0, px(12)))
            if card is not None:
                title, body = split_title(card.text)
                label(inner, title, TITLE_PX, TITLE_FG, weight="bold")
                if body:
                    label(inner, body, BODY_PX, TEXT_FG, pady=(px(8), 0))
            if offer is not None:
                if card is not None:
                    label(inner, offer.prompt, BODY_PX, TITLE_FG, family=SEMIBOLD, pady=(px(20), px(10)))
                else:
                    title, body = split_title(offer.prompt)
                    label(inner, title, TITLE_PX, TITLE_FG, weight="bold")
                    if body:
                        label(inner, body, BODY_PX, TEXT_FG, pady=(px(8), 0))
            row = tk.Frame(inner, bg=CARD_BG)
            row.pack(fill="x", pady=(0 if (offer is not None and card is not None) else px(20), 0))
            if offer is not None:
                real = self._offers.get(offer.id, offer)  # the panel state holds a copy; options live on the Offer
                labels = button_labels(offer.prompt, getattr(real, "options", ("yes", "no")))
                pause = int(getattr(real, "pause_seconds", 0) or 0)
                if pause:
                    self._pause_until.setdefault(offer.id, time.time() + pause)
                width = (inner_w - px(12)) // 2
                for index, (text, choice) in enumerate(zip(labels, ("yes", "no"))):
                    btn = self._button(row, text, lambda c=choice, oid=offer.id: self._pick(oid, c),
                                       primary=index == 0, gap=12 if index == 0 else 0, width=width)
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
                    self._extra_row(inner, offer.id, extra)
                    footer = True
            else:
                actions = card.actions or ("OK",)
                width = (inner_w - px(12) * (len(actions) - 1)) // len(actions)
                for index, text in enumerate(actions):
                    btn = self._button(row, button_text(text), lambda lbl=text: self._card_action(lbl),
                                       primary=index == 0, gap=12 if index < len(actions) - 1 else 0,
                                       width=width)
                    if first_btn is None:
                        first_btn = btn

        win.update_idletasks()
        pad = px(CARD_PAD)
        if tone.toast:
            top_pad = bottom_pad = px(18)
        else:
            top_pad, bottom_pad = pad, (px(12) if footer else pad)
        card_h = top_pad + _int(inner.winfo_reqheight(), px(300)) + bottom_pad
        bear = self._bear_image(tone.bear) if with_bear else None
        bear_w, bear_h = (_int(bear.width(), 0), _int(bear.height(), 0)) if bear is not None else (0, 0)
        stacked = self._reserved is not None and bear is not None
        width = max(card_w, bear_w) if stacked else card_w + (px(BEAR_GAP) + bear_w if bear is not None else 0)
        height = card_h + px(BEAR_GAP) + bear_h if stacked else max(card_h, bear_h)
        card_top = 0 if stacked else height - card_h
        card_bottom = card_top + card_h
        canvas.configure(width=width, height=height)
        # One white surface. The 1px edge stands in for the mock's soft shadow, which Tk can't draw.
        r, tight = px(20), px(4)
        canvas.create_polygon(rounded_rect(0, card_top, card_w - 1, card_bottom - 1, (r, r, tight if bear else r, r)),
                              fill=CARD_BG, outline=CARD_EDGE, width=1)
        canvas.create_window(pad, card_top + top_pad, anchor="nw", window=inner, width=inner_w)
        if bear is not None:
            canvas.create_image(width, height, anchor="se", image=bear)
        if card is None:
            self._offer_has_bear = bear is not None

        x, y = corner_origin(self._work_area(), width, height, px(EDGE_MARGIN), self._reserved)
        if card is None and self._card_win is not None:
            left, top = self._card_box
            x, y = left, top - px(STACK_GAP) - height
        self._place(win, x, y, width, height)
        if card is not None:
            self._card_box = (x, y + card_top)
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

    def _badge(self, parent, text: str, icon: str | None):
        """The status line: the only place amber appears."""
        tk, px = self._tk, self._px
        font = (FONT_FAMILY, -px(16), "bold")
        height = px(32)
        icon_w = px(18) if (icon and self._glyph_font) else 0
        left = px(10)
        text_x = left + (icon_w + px(8) if icon_w else px(2))
        width = text_x + self._text_width(text, font) + px(12)
        badge = tk.Canvas(parent, width=width, height=height, bg=CARD_BG, highlightthickness=0, bd=0)
        half = height / 2
        badge.create_polygon(rounded_rect(0, 0, width - 1, height - 1, (half, half, half, half)),
                             fill=BADGE_BG, outline=BADGE_BG)
        if icon_w:
            badge.create_text(left, half, text=GLYPHS[icon], anchor="w", fill=BADGE_ICON,
                              font=(self._glyph_font, -px(16)))
        badge.create_text(text_x, half, text=text, anchor="w", fill=BADGE_FG, font=font)
        return badge

    def _button(self, row, text: str, command, primary: bool = True, gap: int = 0, width: int = 190):
        """A 64px button with 14px corners: a canvas draws the rounded shape, a flat Tk button inside it
        carries the text, the click, and keyboard focus. Clicking the rounded margin counts too."""
        tk = self._tk
        px = self._px
        height = px(BUTTON_H)
        holder = tk.Canvas(row, width=width, height=height, bg=CARD_BG, highlightthickness=0, bd=0)
        holder.pack(side="left", padx=(0, px(gap)))
        r = px(14)
        fill, edge, ink, hover = ((PRIMARY_BG, PRIMARY_EDGE, PRIMARY_FG, PRIMARY_HOVER) if primary
                                  else (QUIET_BG, QUIET_EDGE, QUIET_FG, QUIET_HOVER))
        border = 2 if primary else 1
        shape = holder.create_polygon(rounded_rect(1, 1, width - 2, height - 2, (r, r, r, r)),
                                      fill=fill, outline=edge, width=border)
        btn = tk.Button(holder, text=text, font=(FONT_FAMILY, -px(BUTTON_PX), "bold"),
                        bg=fill, fg=ink, activebackground=hover, activeforeground=ink,
                        relief="flat", borderwidth=0, highlightthickness=0, cursor="hand2",
                        command=command)
        inset = px(8)
        holder.create_window(width // 2, height // 2, window=btn, width=width - 2 * inset, height=height - 2 * inset)
        holder.bind("<Button-1>", lambda _e: str(btn.cget("state")) != "disabled" and command())

        def paint(color):
            if str(btn.cget("state")) == "disabled":
                return
            holder.itemconfigure(shape, fill=color)
            btn.configure(bg=color)

        for widget in (holder, btn):
            widget.bind("<Enter>", lambda _e: paint(hover))
            widget.bind("<Leave>", lambda _e: paint(fill))
        btn.ally_shape = (holder, shape, fill, edge, border)
        return btn

    def _pause_tick(self, offer_id: int) -> None:
        """Count the 'Continue' button down; it stays locked until the pause is over."""
        current = self._panel.current_offer
        btn = getattr(self, "_no_btn", None)
        if current is None or current.id != offer_id or btn is None:
            return
        left = pause_left(self._pause_until.get(offer_id, 0), time.time())
        try:
            btn.configure(text=no_label(self._no_text, left) + ("s" if left else ""),
                          state="disabled" if left else "normal",
                          bg=DISABLED_BG if left else QUIET_BG, disabledforeground=DISABLED_FG)
            holder, shape, fill, edge, border = btn.ally_shape
            if left:
                holder.itemconfigure(shape, fill=DISABLED_BG, outline=DISABLED_EDGE, dash=(4, 3))
            else:
                holder.itemconfigure(shape, fill=fill, outline=edge, dash="")
            btn.configure(cursor="arrow" if left else "hand2")
        except Exception:
            return
        if left and self._root is not None:
            self._root.after(1000, self._pause_tick, offer_id)

    def _extra_row(self, inner, offer_id: int, extra) -> None:
        """An optional choice that does not answer the question, e.g. 'Tell Priya'. It lives in a quiet
        footer as a text action and confirms in place."""
        tk, px = self._tk, self._px
        tk.Frame(inner, bg=DIVIDER, height=1).pack(fill="x", pady=(px(24), 0))
        row = tk.Frame(inner, bg=CARD_BG)
        row.pack(fill="x", pady=(px(8), 0))
        done = self._extra_done.get(offer_id)
        if done:
            icon = self._glyph(row, "check", GOOD_FG, 20)
            if icon is not None:
                icon.pack(side="left", padx=(px(4), px(8)))
            tk.Label(row, text=done, font=(SEMIBOLD, -px(BUTTON_PX)), fg=TITLE_FG, bg=CARD_BG,
                     wraplength=px(CARD_WIDTH - 2 * CARD_PAD - 32), justify="left", anchor="w").pack(
                side="left", fill="x", pady=px(10))
            return
        label, callback = extra
        start = lambda: self._start_extra(offer_id, label, callback)
        icon = self._glyph(row, "phone", LINK_PRIMARY, 20)
        if icon is not None:
            icon.pack(side="left", padx=(px(4), px(8)))
            icon.configure(cursor="hand2")
            icon.bind("<Button-1>", lambda _e: start())
        link = tk.Button(row, text=label, font=(SEMIBOLD, -px(BUTTON_PX)), fg=LINK_PRIMARY, bg=CARD_BG,
                         activeforeground=PRIMARY_HOVER, activebackground=CARD_BG, relief="flat",
                         borderwidth=0, highlightthickness=0, cursor="hand2", padx=0, pady=px(10),
                         command=start)
        link.pack(side="left")
        link.bind("<Enter>", lambda _e: link.configure(font=(SEMIBOLD, -px(BUTTON_PX), "underline")))
        link.bind("<Leave>", lambda _e: link.configure(font=(SEMIBOLD, -px(BUTTON_PX))))

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

    def _park_offer_above_card(self) -> None:
        """A question that isn't about the visible card stacks just above it, clear of the bear."""
        if self._offer_win is None or self._card_win is None:
            return
        try:
            self._offer_win.update_idletasks()
            height = _int(self._offer_win.winfo_reqheight(), 0)
            left, top = self._card_box
            self._place(self._offer_win, left, top - self._px(STACK_GAP) - height)
            if not _raise_without_focus(self._offer_win):
                self._offer_win.lift()
        except Exception:
            pass

    def _dismiss_toast(self, card) -> None:
        """'Done' notes go away by themselves; anything newer stays."""
        if self._panel.current_card is card and self._panel.current_offer is None:
            self._panel.ok()
            self._render()

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
            self._offer_has_bear = False

    def _destroy_all(self) -> None:
        self._clear_highlight()
        self._close_offer()
        self._close_card()
