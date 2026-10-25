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
# A brand named on its own site is not a scammer's claim (Apple's phone number on apple.com).
_MS = ("microsoft.com", "windows.com", "office.com", "live.com", "outlook.com", "bing.com", "msn.com", "xbox.com", ".microsoft")
_BRAND_HOMES = {"microsoft": _MS, "windows": _MS, "defender": _MS, "apple": ("apple.com", "icloud.com", ".apple"),
                "google": ("google.com", "youtube.com", "gmail.com", "android.com", ".google"),
                "norton": ("norton.com",), "mcafee": ("mcafee.com",)}
# Scam pop-ups pack their claims into a few sentences; a real homepage scatters a brand in the header, a phone number
# in the footer and "malware protection" in a feature list. Signals only add up when they sit this close together.
_WINDOW = 800
# A tel: link counts as a phone number only on short pages (a pop-up), not on a site with a contact link in its footer.
_SHORT_PAGE = 2000


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


def _hits(body: str, host: str) -> list[tuple[int, str, bool]]:
    """(position, signal name, toll-free) for every match; brand names on the brand's own site are skipped."""
    hits = []
    for m in _PHONE.finditer(body):
        raw = m.group()
        digits = re.sub(r"\D", "", raw)
        area = digits[1:4] if len(digits) == 11 else digits[:3]
        if re.search(r"\D", raw) or area in _TOLL_FREE:
            hits.append((m.start(), "phone", area in _TOLL_FREE))
    for name, pattern, _, _ in _RULES[1:]:
        for m in pattern.finditer(body):
            if name == "brand" and host and any(
                    host == d or host.endswith(d if d.startswith(".") else "." + d)
                    for d in _BRAND_HOMES.get(m.group(), ())):
                continue
            hits.append((m.start(), name, False))
    return sorted(hits)


def _best_window(hits, phone_anywhere: bool) -> tuple[int, set, bool]:
    """The densest cluster: (score, signal names, toll-free phone) over any _WINDOW-character span."""
    points = {name: pts for name, _, pts, _ in _RULES}
    best = (0, set(), False)
    starts = [pos for pos, _, _ in hits] or [0]
    for start in starts:
        names, toll = set(), False
        for pos, name, free in hits:
            if start <= pos <= start + _WINDOW:
                names.add(name)
                toll = toll or free
        if phone_anywhere:
            names.add("phone")
        score = sum(points[n] for n in names) + (1 if toll else 0)
        if score > best[0]:
            best = (score, names, toll)
    return best


def score_text(text: str, url: str = "", overlay_text: str = "", fullscreen: bool = False,
               has_tel_link: bool = False, extra_allow: tuple[str, ...] = ()) -> TextScore:
    """Score the densest cluster of signals, with a bonus for each signal also inside an overlay."""
    body = normalize(text + " " + overlay_text)
    overlay = normalize(overlay_text)
    host = _hostname(url) if url else ""
    score, names, _ = _best_window(_hits(body, host), has_tel_link and len(body) <= _SHORT_PAGE)
    overlay_names = {name for _, name, _ in _hits(overlay, host)} if overlay else set()
    overlay_hit = bool(names & overlay_names)
    score += len(names & overlay_names)
    signals = [name for name, _, _, _ in _RULES if name in names]
    phrases = [phrase for name, _, _, phrase in _RULES if name in names]
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
