"""Pure layout, text, and command-queue logic for the overlay (no tkinter)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

MAX_TEXT_LEN = 400
CARD_TOP_MARGIN = 40
CARD_MAX_WIDTH = 900
CARD_WIDTH_FRAC = 0.8
HIGHLIGHT_BORDER = 6

INFO_BG = "#FFFFFF"
INFO_FG = "#1A1A1A"
RISK_BG = "#FFF4D6"
RISK_FG = "#1A1A1A"
RISK_BORDER = "#B26A00"
HIGHLIGHT_COLOR = "#B26A00"


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
