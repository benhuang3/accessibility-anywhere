"""Pure, standard-library page-text scoring for risk rule R1."""

from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import urlsplit


@dataclass(frozen=True)
class TextScore:
    score: int
    severity: str | None
    signals: tuple[str, ...]
    evidence: str
    allowlisted: bool


_PHONE = re.compile(r"\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_TOLL_FREE = frozenset({"800", "833", "844", "855", "866", "877", "888"})
_RULES = (
    ("phone", _PHONE, 3, "a phone number"),
    ("brand", re.compile(r"\b(?:microsoft|windows|apple|defender|norton|mcafee|google)\b"), 2,
     "a technology brand"),
    ("alarm", re.compile(r"\b(?:viruses|virus|infected|malware|trojan|hacked|locked|blocked|security alert|do not (?:close|restart|shut down|shutdown|turn off))\b"), 2,
     "an alarming claim"),
    ("action", re.compile(r"\b(?:call (?:now|immediately|support|toll)|contact (?:support|technician))\b"), 2,
     "a request to call support"),
    ("payment", re.compile(r"\b(?:gift cards?|bitcoin|crypto(?:currency)?|wire transfer)\b"), 1,
     "a payment request"),
)
_ALLOW = ("microsoft.com", "learn.microsoft.com", "support.microsoft.com", "ftc.gov",
          "consumer.ftc.gov", "fbi.gov", "ic3.gov", "cisa.gov", "aarp.org", "wikipedia.org")
# Only governments and universities can register these endings, so scam pages are never on them.
_ALLOW_SUFFIXES = (".gov", ".mil", ".edu")


def _phone_matches(text: str) -> list[tuple[str, bool]]:
    """Phone-looking runs as (area code, toll_free). A bare run of 10 digits with no spacing, dashes or
    parentheses (an order or tracking number) only counts when its area code is toll-free."""
    out = []
    for m in _PHONE.finditer(text):
        raw = m.group()
        digits = re.sub(r"\D", "", raw)
        area = digits[1:4] if len(digits) == 11 else digits[:3]
        if re.search(r"\D", raw) or area in _TOLL_FREE:
            out.append((area, area in _TOLL_FREE))
    return out


def normalize(text: str) -> str:
    """Normalize compatibility characters, format characters, case and spacing."""
    text = unicodedata.normalize("NFKC", text)
    return " ".join("".join(c for c in text if unicodedata.category(c) != "Cf").lower().split())


def redact(s: str) -> str:
    """Remove phone-like runs containing at least seven digits."""
    return re.sub(r"\+?(?:\(?\d[\s.()\-]*){6,}\d\)?", "a phone number", s)


def _hostname(url: str) -> str:
    try:
        value = url.strip()
        parsed = urlsplit(value if "://" in value or value.startswith("//") else "//" + value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host or any(not label or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                           for label in host.split(".")):
            return ""
        return host
    except ValueError:
        return ""


def is_allowlisted(url: str, extra_allow: tuple[str, ...] = ()) -> bool:
    """Match parsed hosts and their subdomains, never URL substrings."""
    host = _hostname(url)
    if not host:
        return False
    if host.endswith(_ALLOW_SUFFIXES):
        return True
    return any(domain and (host == domain or host.endswith("." + domain))
               for domain in (_hostname(item) for item in _ALLOW + extra_allow))


def score_text(text: str, url: str = "", overlay_text: str = "", fullscreen: bool = False,
               has_tel_link: bool = False, extra_allow: tuple[str, ...] = ()) -> TextScore:
    """Score each signal once, with an additional bonus for overlay matches."""
    body = normalize(text + " " + overlay_text)
    overlay = normalize(overlay_text)
    score = 0
    signals = []
    phrases = []
    overlay_hit = False
    for name, pattern, points, phrase in _RULES:
        if name == "phone":
            hit, in_overlay = bool(_phone_matches(body)) or has_tel_link, bool(_phone_matches(overlay))
        else:
            hit, in_overlay = bool(pattern.search(body)), bool(pattern.search(overlay))
        if hit:
            signals.append(name)
            phrases.append(phrase)
            score += points + in_overlay
            overlay_hit = overlay_hit or in_overlay
    if any(toll_free for _, toll_free in _phone_matches(body)):
        score += 1
    if fullscreen:
        signals.append("fullscreen")
        phrases.append("a full screen page")
        score += 2
    allowed = is_allowlisted(url, extra_allow)
    severity = None
    if not allowed:
        # "high" also needs a brand claim, a full-screen page or a signal inside an overlay. A phone number plus
        # an alarming word alone matches ordinary bank and shipping help pages, so that stays "medium".
        if (score >= 7 and "phone" in signals and "alarm" in signals
                and ("brand" in signals or "fullscreen" in signals or overlay_hit)):
            severity = "high"
        elif score >= 4 and len(signals) >= 2:
            # One signal alone (a contact number on a help page) is not a scam.
            severity = "medium"
    evidence = "Page signals: " + ", ".join(phrases) + "." if phrases else "No page signals."
    return TextScore(score, severity, tuple(signals), evidence, allowed)
