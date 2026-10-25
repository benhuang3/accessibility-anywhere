# Task: R4 card-field detection and payment-risk scoring as pure functions (Ally risk rules)

Read AGENTS.md and `ally/risk/text.py` (already in this worktree; match its style: small functions, frozen dataclass results, stdlib only). Python 3.11+. Work only in this worktree.

## Goal
A browser content script will send a plain description of the form fields on a page (never what the user typed). Decide (1) whether the page asks for card details and (2) how risky that is given what else is known. PURE functions, no `ally` imports (no `ally.contracts`), no network.

## Allowed files (touch nothing else)
- `ally/risk/fields.py` (new)
- `tests/test_risk_r4.py` (new)
Do NOT create `ally/risk/__init__.py` (implicit namespace package; verify `from ally.risk.fields import detect_card_fields` imports).

## Public API (exactly these names)

```python
@dataclass(frozen=True)
class CardFields:
    present: bool
    count: int                    # how many fields looked like card fields
    signals: tuple[str, ...]      # subset of: "autocomplete", "name", "length"

@dataclass(frozen=True)
class PaymentRisk:
    severity: str | None          # "high", "medium" or None
    reasons: tuple[str, ...]      # subset of: "unfamiliar_site", "insecure_page", "lookalike", "remote_session"
    evidence: str                 # one plain sentence, no digits, never a URL

def detect_card_fields(fields: list[dict]) -> CardFields
def score_payment(card: CardFields, domain_known: bool, insecure: bool = False,
                  lookalike: bool = False, remote_session: bool = False) -> PaymentRisk
```

## detect_card_fields
Each field is a dict with optional keys: `type`, `name`, `id`, `autocomplete`, `placeholder`, `label` (all strings), `maxlength` (int or numeric string or None), `inputmode` (string). Missing or wrongly typed values must never raise (ignore them). A field counts as a card field when ANY of:
- `autocomplete` (lowercased, trimmed) starts with `cc-` (cc-number, cc-csc, cc-exp, cc-exp-month, cc-exp-year, cc-name). Signal `autocomplete`.
- `name`, `id`, `placeholder` or `label` matches (case-insensitive, hyphens/underscores/spaces ignored) any of: card number, cardnumber, card no, ccnum, cc number, cvv, cvc, cvn, security code, card expiry, expiration date, exp date, expiry. Signal `name`. Do NOT match unrelated words: "discard", "cardigan", "scorecard", "expired session", "security question" must not count.
- `maxlength` between 13 and 19 inclusive AND (`type` is `tel` or `number` or `text`) AND `inputmode` is `numeric`/`decimal` or `type` is `tel`/`number`. Signal `length`. A plain text input with maxlength 16 and no numeric hint does NOT count.
Password fields (`type` "password") never count. `present` is True when at least one field counts. `signals` is the sorted unique set of signals seen. Cap work at the first 200 fields.

## score_payment
- `present` False: severity None, reasons ().
- Reasons: `unfamiliar_site` when `domain_known` is False; `insecure_page` when `insecure`; `lookalike` when `lookalike`; `remote_session` when `remote_session`.
- HIGH if `present` and (`lookalike` or `remote_session`).
- else MEDIUM if `present` and (`unfamiliar_site` or `insecure_page`).
- else None (a known site over https, with no other risk, never warns).
- `evidence`: one calm hedged sentence chosen for the top reason (fixed text per reason, for example "This page asks for card details and the website is not one I recognize."), no digits, no URL.

## Tests (tests/test_risk_r4.py, unittest; mirror the header style of tests/test_risk_r1.py; run from the tests folder)
- autocomplete cc-number, cc-csc, cc-exp detected; `autocomplete="off"` alone is not
- name/id/placeholder patterns: "cardnumber", "card-number", "Card Number", "ccnum", "CVV", "cvc2", "Security Code", "expiry date" detected
- not detected: "discard", "cardigan", "scorecard", "username", "email", "address-line1", "zip", "security question", "expired session", a password field named "cvv_password"
- maxlength rule: type=tel maxlength 16 counts; type=text maxlength 16 with no numeric hint does not; type=tel maxlength 10 does not; maxlength "19" as a string counts; maxlength 20 does not
- junk input: None entries, non-dict entries, ints, missing keys, 500 fields, never raise
- score_payment: not present -> None; present + known + https -> None; present + unknown site -> medium; present + known + insecure -> medium; present + lookalike -> high; present + remote_session -> high; high wins over medium
- no digits in evidence for any combination; determinism; the module source has no import from `ally`
Run `cd tests && python -m unittest test_risk_r4`. All must pass.

## Out of scope
Everything else: no Risk objects, no bridge/bus/agent, no extension, no other rules, no config, no git commits (leave changes uncommitted).
