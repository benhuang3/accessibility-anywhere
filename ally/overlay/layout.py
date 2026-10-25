"""Pure layout, text, and command-queue logic for the overlay (no tkinter)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

MAX_TEXT_LEN = 400
HIGHLIGHT_BORDER = 6

# Companion layout (design/mock): the bear sits in the bottom-right corner of the work area and the
# card, its speech, sits 12px to its left with the bottoms aligned. Sizes are for a 96-DPI screen.
CARD_WIDTH = 440
CARD_PAD = 24
BEAR_WIDTH = 140
BEAR_WIDTHS = (140, 175, 210, 280)  # sprites in bear/, one per common display scaling
BEAR_GAP = 12
EDGE_MARGIN = 24
STACK_GAP = 12
TOAST_SECONDS = 6.0

# Companion palette: a fixed light card (every text pair meets WCAG AAA, 7:1).
CARD_BG = "#FFFFFF"
CARD_EDGE = "#D6D3CD"
TITLE_FG = "#111827"
TEXT_FG = "#374151"
DIVIDER = "#E5E7EB"
BADGE_BG = "#FEF3C7"
BADGE_FG = "#78350F"
BADGE_ICON = "#D97706"
GOOD_FG = "#15803D"
PRIMARY_BG = "#115E59"
PRIMARY_HOVER = "#0D4D49"
PRIMARY_EDGE = "#0B3F3C"
PRIMARY_FG = "#FFFFFF"
QUIET_BG = "#F3F1EC"
QUIET_HOVER = "#E9E6DF"
QUIET_EDGE = "#D6D3CD"
QUIET_FG = "#1F2937"
DISABLED_BG = "#F3F4F6"
DISABLED_FG = "#6B7280"
DISABLED_EDGE = "#D1D5DB"
LINK_PRIMARY = "#115E59"
LINK_FG = "#4B5563"
KEY_COLOR = "#FE01FD"  # keyed out to make the window borderless; appears nowhere in the design
HIGHLIGHT_COLOR = "#1D4ED8"
MERGE_SECONDS = 1.5


def split_title(text: str) -> tuple[str, str]:
    """Keep the first sentence's punctuation with the title."""
    text = sanitize_text(text).strip()
    endings = [i for mark in (". ", "? ") if (i := text.find(mark)) >= 0]
    if not endings:
        return text, ""
    end = min(endings) + 1
    return text[:end], text[end:].strip()


def pause_left(deadline: float, now: float) -> int:
    """Whole seconds left on a pause (0 once it is over)."""
    return max(0, int(deadline - now + 0.999))


def no_label(base: str, seconds_left: int) -> str:
    """While a pause runs, the 'keep going' button counts down instead of offering to skip the warning."""
    return f"Continue in {seconds_left}" if seconds_left > 0 else base


def button_labels(prompt: str, options: Sequence[str] = ("yes", "no")) -> tuple[str, str]:
    if len(options) == 2 and tuple(options) != ("yes", "no"):
        return sanitize_text(options[0]), sanitize_text(options[1])
    if sanitize_text(prompt).startswith("Want me to close"):
        return "Yes, close it", "Keep it open"
    return "Yes", "No"


def button_text(label: str) -> str:
    """A verb on every button: a bare 'OK' reads as 'Got it'."""
    return "Got it" if label.strip().upper() == "OK" else label


@dataclass(frozen=True)
class Tone:
    """How urgent a panel looks: the status badge, its icon, the bear's face, and whether it is a toast."""
    badge: str | None
    icon: str | None
    bear: str
    toast: bool = False


def card_tone(card: "CardContent | None", offer: "OfferContent | None") -> Tone:
    """Urgency lives in words and the bear's face, never in the card colour."""
    if card is not None and card.kind == "done":
        return Tone(None, "check", "happy", toast=True)
    if card is not None and card.kind == "voice":
        bear = {"listening": "listening", "thinking": "idle", "speaking": "happy"}.get(card.voice_state, "idle")
        return Tone(None, None, bear)
    if card is not None and card.kind == "risk":
        _, body = split_title(card.text)
        if offer is None and not body and card.text.startswith("Closed"):
            return Tone(None, "check", "happy", toast=True)  # an action result: "Closed the page."
        return Tone("Safety check", "shield", "worried" if (body or offer) else "concerned")
    if card is not None:
        return Tone(None, None, "happy")
    if offer is not None and offer.context == "risk":
        return Tone("Just checking", "question", "concerned")
    return Tone(None, None, "listening")


def bear_width(scale: float) -> int:
    """The sprite width closest to the bear's size at this display scaling."""
    want = BEAR_WIDTH * scale
    return min(BEAR_WIDTHS, key=lambda w: abs(w - want))


def corner_origin(work: tuple[int, int, int, int], width: int, height: int,
                  margin: int = EDGE_MARGIN,
                  reserved: tuple[int, int, int, int] | None = None) -> tuple[int, int]:
    """Top-left corner that puts a width x height window in the bottom-right of the work area
    (left, top, right, bottom), or just above a reserved call rect with right edges aligned."""
    left, top, right, bottom = work
    if reserved is not None:
        _, reserved_top, reserved_right, _ = reserved
        return max(left, min(right - width, reserved_right - width)), max(top, reserved_top - STACK_GAP - height)
    return max(left, right - margin - width), max(top, bottom - margin - height)


def rounded_rect(x0: float, y0: float, x1: float, y1: float,
                 radii: tuple[float, float, float, float], steps: int = 6) -> list[float]:
    """Polygon points for a rectangle with per-corner radii (top-left, top-right, bottom-right, bottom-left)."""
    import math

    tl, tr, br, bl = radii
    corners = ((x0 + tl, y0 + tl, tl, 180), (x1 - tr, y0 + tr, tr, 270),
               (x1 - br, y1 - br, br, 0), (x0 + bl, y1 - bl, bl, 90))
    points: list[float] = []
    for cx, cy, r, start in corners:
        for i in range(steps + 1):
            a = math.radians(start + 90 * i / steps)
            points += [cx + r * math.cos(a), cy + r * math.sin(a)]
    return points


@dataclass(frozen=True)
class CardContent:
    text: str
    actions: tuple[str, ...]
    kind: str
    voice_state: str = ""


@dataclass(frozen=True)
class OfferContent:
    id: int
    context: str
    prompt: str


class PanelState:
    """Presentation state, independent of Tk and of the offer's answer transport."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.current_card: CardContent | None = None
        self.current_offer: OfferContent | None = None
        self._offer_at = 0.0
        self.merged = False

    @property
    def view(self) -> tuple[str, CardContent | None, OfferContent | None]:
        if self.merged:
            mode = "panel"
        elif self.current_card is not None:
            mode = "card+bubble" if self.current_offer is not None else "card"
        else:
            mode = "bubble" if self.current_offer is not None else "none"
        return mode, self.current_card, self.current_offer

    def card(self, text: str, actions: Sequence[str] = ("OK",), kind: str = "info",
             t: float | None = None, voice_state: str = "") -> tuple:
        if self.current_card is not None and self.current_card.kind == "risk" and kind in ("info", "voice"):
            return self.view
        if kind == "voice" and (self.current_offer is not None
                                or (self.current_card is not None and self.current_card.kind == "done")):
            return self.view
        now = self.clock() if t is None else t
        self.current_card = CardContent(sanitize_text(text), tuple(sanitize_text(a) for a in actions), kind, voice_state)
        offer = self.current_offer
        self.merged = bool(kind == "risk" and offer is not None and offer.context == "risk"
                           and (self.merged or 0 <= now - self._offer_at <= MERGE_SECONDS))
        return self.view

    def offer(self, offer_id: int, context: str, prompt: str, t: float | None = None) -> tuple:
        if self.current_card is not None and self.current_card.kind == "voice":
            self.current_card = None
        self.current_offer = OfferContent(offer_id, context, sanitize_text(prompt))
        self._offer_at = self.clock() if t is None else t
        # A visible warning can accept a new decision at any age. An earlier
        # question only joins a newly arriving warning within the grace period.
        self.merged = bool(context == "risk" and self.current_card is not None
                           and self.current_card.kind == "risk")
        return self.view

    def offer_done(self, offer_id: int) -> tuple:
        if self.current_offer is not None and self.current_offer.id == offer_id:
            self.current_offer = None
            self.merged = False
        return self.view

    def answer(self, offer_id: int) -> tuple:
        """An overlay decision dismisses its merged warning as well."""
        if self.current_offer is not None and self.current_offer.id == offer_id:
            if self.merged:
                self.current_card = None
            self.offer_done(offer_id)
        return self.view

    def clear(self) -> tuple:
        self.current_offer = None
        self.merged = False
        return self.view

    def ok(self) -> tuple:
        self.current_card = None
        self.merged = False
        return self.view


def sanitize_text(text: str, max_len: int = MAX_TEXT_LEN) -> str:
    """Plain text only: strip control characters, cap length."""
    cleaned = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    if len(cleaned) > max_len:
        return cleaned[:max_len]
    return cleaned


def wrap_lines(text: str, chars_per_line: int) -> list[str]:
    """Word-wrap to a character budget per line (no truncation of words mid-way when possible)."""
    if chars_per_line < 1:
        chars_per_line = 1
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current: list[str] = []
        length = 0
        for word in words:
            extra = len(word) + (1 if current else 0)
            if current and length + extra > chars_per_line:
                lines.append(" ".join(current))
                current = [word]
                length = len(word)
            else:
                if current:
                    length += 1 + len(word)
                else:
                    length = len(word)
                current.append(word)
        if current:
            lines.append(" ".join(current))
    return lines


def make_card_command(text: str, actions: Sequence[str], kind: str) -> tuple[Any, ...]:
    return ("card", sanitize_text(text), tuple(actions), kind)


def make_offer_command(offer_id: int, prompt: str) -> tuple[Any, ...]:
    return ("offer", offer_id, sanitize_text(prompt))


def make_highlight_command(box) -> tuple[Any, ...]:
    return ("highlight", box)


def make_clear_command() -> tuple[Any, ...]:
    return ("clear",)


@dataclass
class DisplayState:
    """Tracks visible card kind for replace rules (risk beats info)."""

    card_kind: str | None = None

    def may_show_card(self, kind: str) -> bool:
        if self.card_kind == "risk" and kind in ("info", "voice"):
            return False
        if self.card_kind == "done" and kind == "voice":
            return False
        return True

    def note_card(self, kind: str) -> None:
        self.card_kind = kind

    def clear_card(self) -> None:
        self.card_kind = None


@dataclass
class CommandView:
    """Tk-less mirror of what the overlay would show."""

    card: tuple[Any, ...] | None = None
    card_kind: str | None = None
    offer: tuple[Any, ...] | None = None
    highlight: Any = None

    def apply(self, cmd: tuple[Any, ...]) -> bool:
        kind = cmd[0]
        if kind == "card":
            _, text, actions, card_kind = cmd
            if self.card_kind == "risk" and card_kind in ("info", "voice"):
                return False
            if card_kind == "voice" and (self.offer is not None or self.card_kind == "done"):
                return False
            self.card = (text, actions, card_kind)
            self.card_kind = card_kind
            return True
        if kind == "offer":
            if self.card_kind == "voice":
                self.card = self.card_kind = None
            self.offer = cmd
            return True
        if kind == "highlight":
            self.highlight = cmd[1]
            return True
        if kind == "clear":
            self.card = None
            self.card_kind = None
            self.offer = None
            self.highlight = None
            return True
        return False


def answer_offer(offer, choice: str) -> bool:
    """Resolve an offer from the overlay; only the first answer counts."""
    return offer.resolve(choice, "overlay")


def key_to_choice(keysym: str) -> str | None:
    if keysym in ("Return", "KP_Enter"):
        return "yes"
    if keysym == "Escape":
        return "no"
    return None


def highlight_bars(box: tuple[int, int, int, int], border: int = HIGHLIGHT_BORDER) -> list[tuple[int, int, int, int]]:
    """Four rectangles (left, top, width, height) for an outline around box."""
    left, top, width, height = box
    b = border
    return [
        (left, top, width, b),
        (left, top + height - b, width, b),
        (left, top, b, height),
        (left + width - b, top, b, height),
    ]
