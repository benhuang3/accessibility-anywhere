"""Pure, standard-library card-field detection and payment scoring for R4."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CardFields:
    present: bool
    count: int
    signals: tuple[str, ...]


@dataclass(frozen=True)
class PaymentRisk:
    severity: str | None
    reasons: tuple[str, ...]
    evidence: str


# Card-field hints as token phrases. Names, ids, placeholders and labels are split into lowercase tokens
# (camelCase, punctuation and spaces all separate tokens), so "credit_card_number", "paymentCardNumber" and
# "Enter your card number" match while "discard", "scorecard", "cardnotes" and "expired session" do not.
_PHRASES = frozenset({
    ("card", "number"), ("card", "no"), ("cc", "number"), ("cc", "num"), ("cardnumber",), ("cardno",),
    ("ccnum",), ("ccnumber",), ("cc", "exp"), ("cc", "csc"), ("cvv",), ("cvc",), ("cvc2",), ("cvn",), ("security", "code"), ("securitycode",),
    ("card", "expiry"), ("card", "expiration"), ("cardexpiry",), ("expiry", "date"), ("expirydate",),
    ("expiration", "date"), ("expirationdate",), ("exp", "date"), ("expdate",), ("expiry",),
})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_EVIDENCE = {
    "lookalike": "This page asks for card details and the website may resemble another site.",
    "remote_session": "This page asks for card details while someone may have remote access.",
    "unfamiliar_site": "This page asks for card details and the website is not one I recognize.",
    "insecure_page": "This page asks for card details and the connection may not be secure.",
}


def _string(field: dict, key: str) -> str:
    value = field.get(key)
    return value.strip().lower() if isinstance(value, str) else ""


def _mentions_card(field: dict, keys=("name", "id", "placeholder", "label")) -> bool:
    for key in keys:
        value = field.get(key)
        if not isinstance(value, str):
            continue
        tokens = re.findall(r"[a-z0-9]+", _CAMEL.sub(" ", value[:200]).lower())
        for size in (1, 2):
            if any(tuple(tokens[i:i + size]) in _PHRASES for i in range(len(tokens) - size + 1)):
                return True
    return False


def _numeric_length(value: object) -> bool:
    if type(value) is int:
        return 13 <= value <= 19
    if isinstance(value, str):
        return value.strip() in {str(n) for n in range(13, 20)}
    return False


def detect_card_fields(fields: list[dict]) -> CardFields:
    """Inspect metadata for at most the first two hundred fields."""
    count = 0
    signals = set()
    if not isinstance(fields, (list, tuple)):
        return CardFields(False, 0, ())
    for field in fields[:200]:
        if not isinstance(field, dict):
            continue
        kind = _string(field, "type")
        if kind == "password":
            continue
        found = set()
        if _string(field, "autocomplete").startswith("cc-"):
            found.add("autocomplete")
        if _mentions_card(field):
            found.add("name")
        if (kind in {"tel", "number", "text"}
                and _numeric_length(field.get("maxlength"))
                and (kind in {"tel", "number"}
                     or _string(field, "inputmode") in {"numeric", "decimal"})):
            found.add("length")
        if found:
            count += 1
            signals.update(found)
    return CardFields(count > 0, count, tuple(sorted(signals)))


def score_payment(card: CardFields, domain_known: bool, insecure: bool = False,
                  lookalike: bool = False, remote_session: bool = False) -> PaymentRisk:
    """Choose the strongest payment warning using fixed, metadata-free evidence."""
    if not card.present:
        return PaymentRisk(None, (), "No card fields were detected.")
    reasons = tuple(reason for reason, active in (
        ("unfamiliar_site", not domain_known), ("insecure_page", insecure),
        ("lookalike", lookalike), ("remote_session", remote_session)) if active)
    severity = "high" if lookalike or remote_session else "medium" if reasons else None
    top = next((reason for reason in _EVIDENCE if reason in reasons), None)
    evidence = _EVIDENCE[top] if top else "This page appears to ask for card details."
    return PaymentRisk(severity, reasons, evidence)
