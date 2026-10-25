"""Pure layout, text, and command-queue logic for the overlay (no tkinter)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

MAX_TEXT_LEN = 400
CARD_TOP_MARGIN = 40
CARD_MAX_WIDTH = 900
CARD_WIDTH_FRAC = 0.8
HIGHLIGHT_BORDER = 6

INFO_BG = "#FFFFFF"
INFO_FG = "#1A1A1A"
RISK_BG = "#FFF8E6"
RISK_FG = "#1A1A1A"
RISK_BORDER = "#B26A00"
HIGHLIGHT_COLOR = "#B26A00"
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
        return "Yes, close it", "No, keep it"
    return "Yes", "No"


@dataclass(frozen=True)
class CardContent:
    text: str
    actions: tuple[str, ...]
    kind: str


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
             t: float | None = None) -> tuple:
        if self.current_card is not None and self.current_card.kind == "risk" and kind == "info":
            return self.view
        now = self.clock() if t is None else t
        self.current_card = CardContent(sanitize_text(text), tuple(sanitize_text(a) for a in actions), kind)
        offer = self.current_offer
        self.merged = bool(kind == "risk" and offer is not None and offer.context == "risk"
                           and (self.merged or 0 <= now - self._offer_at <= MERGE_SECONDS))
        return self.view

    def offer(self, offer_id: int, context: str, prompt: str, t: float | None = None) -> tuple:
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


def fit_card(screen_w: int, screen_h: int) -> tuple[int, int, int]:
    """Top-center placement: y=40px, width 900px (45% of a very wide screen), never over 80% of the screen."""
    w = min(max(CARD_MAX_WIDTH, int(screen_w * 0.45)), int(screen_w * CARD_WIDTH_FRAC))
    x = max(0, (screen_w - w) // 2)
    y = CARD_TOP_MARGIN
    return x, y, w


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
        if self.card_kind == "risk" and kind == "info":
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
            if self.card_kind == "risk" and card_kind == "info":
                return False
            self.card = (text, actions, card_kind)
            self.card_kind = card_kind
            return True
        if kind == "offer":
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
