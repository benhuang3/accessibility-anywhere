"""Warning wording for scam and risk protection, loaded from copy.toml.

Pure apart from reading that one file. Filling in {tool} / {host} / {usual_site} never lets a phone
number through: every filled-in value goes through redact().
"""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from .text import redact

_DEFAULT = Path(__file__).with_name("copy.toml")
_FALLBACK = {"tool": "a remote-control tool", "host": "this website", "usual_site": "its usual address"}
_KINDS = {"scam_popup", "remote_access", "untrusted_site", "payment_field"}


@dataclass(frozen=True)
class Action:
    id: str
    label: str


@dataclass(frozen=True)
class WarningCopy:
    key: str
    title: str
    body: str
    spoken: str
    actions: tuple[Action, ...]   # the first one is the safe default


class _Safe(dict):
    def __missing__(self, key):
        return ""


def load_copy(path: Path | str | None = None) -> dict:
    with open(path or _DEFAULT, "rb") as f:
        return tomllib.load(f)


def copy_key(kind: str, usual_site: str = "", joined: bool = False) -> str:
    """Which copy.toml section to use for a Risk kind. `joined` = remote tool running with a payment page."""
    if kind not in _KINDS:
        raise ValueError(f"no warning wording for risk kind {kind!r}")
    if kind == "remote_access" and joined:
        return "joined_remote_payment"
    if kind == "untrusted_site" and not usual_site:
        return "untrusted_site_generic"
    return kind


def _clean(value: str, fallback: str) -> str:
    text = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).split())[:80]
    return redact(text) if text else fallback


def render(key: str, path: Path | str | None = None, **fields) -> WarningCopy:
    section = load_copy(path)[key]
    values = _Safe({name: _clean(fields.get(name), default) for name, default in _FALLBACK.items()})
    def fill(text: str) -> str:
        out = text.format_map(values)
        return out[:1].upper() + out[1:]  # a fallback like "a remote-control tool" can open a sentence

    actions = tuple(Action(a["id"], fill(a["label"])) for a in section["actions"])
    return WarningCopy(key, fill(section["title"]), fill(section["body"]), fill(section["spoken"]), actions)
