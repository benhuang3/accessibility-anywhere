"""Soft scam signals: too weak to warn on their own, enough to justify a second look.

R1 needs a phone number plus alarm words before it warns. Scams that lean on secrecy, remote access or money pressure
can slip under it, so two of these soft signals (or one with a phone number) send the page to the vision review,
which may confirm a scam or stay quiet. Pure standard library.
"""

import re

_SOFT = (
    ("remote_request", re.compile(
        r"\b(?:remote (?:access|tool|session|support|control)|anydesk|teamviewer|quick assist|screen shar\w+|"
        r"download and run|let (?:us|our \w+) (?:connect|control|access))\b")),
    ("secrecy", re.compile(
        r"\b(?:do not tell|don'?t tell|keep this (?:page|window|tab) open|do not hang up|don'?t hang up|"
        r"stay on the (?:line|phone)|do not (?:discuss|share) this)\b")),
    ("money_pressure", re.compile(
        r"\b(?:gift cards?|wire transfer|bitcoin|crypto(?:currency)?|western union|zelle|"
        r"refund\w* .{0,60}(?:mistake|error|overpa\w*)|(?:mistake|error|overpa\w*) .{0,60}refund\w*|reverse (?:it|the))\b")),
    ("urgent_threat", re.compile(
        r"\b(?:arrest|warrant|legal action|final notice|account (?:will be )?(?:closed|suspended|terminated)|"
        r"within \d+ (?:minutes|hours))\b")),
)

FLAG_MEANINGS = {
    "remote_request": "The page talks about letting someone control the computer.",
    "secrecy": "The page tells you to stay on the page or not to tell anyone.",
    "money_pressure": "The page mentions gift cards, transfers, cryptocurrency or a refund mistake.",
    "urgent_threat": "The page threatens arrest, legal action or closing an account.",
    "phone_prompt": "The page asks you to call a number.",
}


def soft_flags(text: str, has_phone: bool = False) -> tuple[str, ...]:
    body = " ".join((text or "").lower().split())[:20000]
    flags = [name for name, pattern in _SOFT if pattern.search(body)]
    if has_phone:
        flags.append("phone_prompt")
    return tuple(flags)


def soft_gate(flags: tuple[str, ...]) -> bool:
    """Two different weak signs; a phone number counts as one of them."""
    return len(set(flags)) >= 2
