# Task: R1 scam-text scoring as a pure function (Ally risk rules, step 1)

Read AGENTS.md first. Python 3.11+, standard library only. Work only in this worktree.

## Goal
Implement rule R1 ("scam_popup from page text") exactly as specified below, as a PURE function with tests. It must NOT import anything from `ally` (no `ally.contracts`, no bridge, no bus): the foundation package is not on this branch, and a thin adapter that turns the result into a `Risk` will be written later by someone else.

## Allowed files (touch nothing else)
- `ally/risk/text.py` (new)
- `tests/test_risk_r1.py` (new)
Do NOT create `ally/risk/__init__.py` (another branch adds it; `ally/risk` must work as an implicit namespace package, so `from ally.risk.text import score_text` must import fine; verify it).

## Public API (exactly these names)

```python
@dataclass(frozen=True)
class TextScore:
    score: int
    severity: str | None        # "high", "medium" or None
    signals: tuple[str, ...]    # category names that matched, in the fixed order below
    evidence: str               # plain words naming the signals; never page text, never digits
    allowlisted: bool

def score_text(text: str, url: str = "", overlay_text: str = "", fullscreen: bool = False,
               has_tel_link: bool = False, extra_allow: tuple[str, ...] = ()) -> TextScore
def is_allowlisted(url: str, extra_allow: tuple[str, ...] = ()) -> bool
def normalize(text: str) -> str      # NFKC, drop zero-width/format chars (Unicode category Cf), lowercase, collapse whitespace
def redact(s: str) -> str            # replace any phone-like run of 7+ digits (spaces . - ( ) and leading + allowed) with "a phone number"
```

## Scoring (the accepted spec; do not change the weights)
Analyse `normalize(text + " " + overlay_text)`. Each category scores ONCE:

| signal | pattern | points |
|---|---|---|
| `phone` | a phone number (regex below) OR `has_tel_link` | +3, and +1 more if the number's area code is 800/833/844/855/866/877/888 |
| `brand` | whole word: microsoft, windows, apple, defender, norton, mcafee, google | +2 |
| `alarm` | virus(es), infected, malware, trojan, hacked, locked, blocked, security alert, `do not (close|restart|shut down|shutdown|turn off)` | +2 |
| `action` | `call (now|immediately|support|toll)` or `contact (support|technician)` | +2 |
| `payment` | gift card(s), bitcoin, crypto(currency), wire transfer | +1 |
| `fullscreen` | the `fullscreen` argument is True | +2 |

Phone regex (run on the normalized text): `\b(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b`. Use word boundaries and allow plurals so "viruses" and "gift cards" match; "pineapple" must not match "apple", "windowsill" must not match "windows".

Overlay weighting: if a category among phone, brand, alarm, action, payment also matches inside `normalize(overlay_text)` alone, add +1 extra for that category (at most one bonus per category).

Severity:
- allowlisted host (see below): `severity=None`, `allowlisted=True` (the score is still computed and returned)
- `score >= 7` AND `phone` AND `alarm` both present: `"high"`
- otherwise `score >= 4`: `"medium"` (a nudge, never a block)
- otherwise `None`

`signals` lists matched category names in this fixed order: phone, brand, alarm, action, payment, fullscreen.
`evidence` is a short plain sentence naming the signals in words (for example "Page signals: a phone number, a virus claim, a request to call support"), built from fixed phrases per signal. It must never include page text and must contain no digits at all.

## Allowlist
Hosts (and any subdomain of them): microsoft.com, learn.microsoft.com, support.microsoft.com, ftc.gov, consumer.ftc.gov, fbi.gov, ic3.gov, cisa.gov, aarp.org, plus anything in `extra_allow` (the user's bank domains). Match on the parsed hostname: lowercase, no port, no trailing dot, handle a missing scheme. It must be correct for: `https://ftc.gov@evil.example/` (host is evil.example, NOT allowlisted), `https://ftc.gov.evil.example`, `https://notftc.gov`, `https://MICROSOFT.COM./x`, `consumer.ftc.gov:443`, an empty string, garbage input. Note `microsoft.com` itself is allowlisted but `windows.microsoft.com.evil.example` is not.

## Tests (tests/test_risk_r1.py, unittest, mirror the header style of tests/test_registry.py; run from the tests folder)
Cover at least:
- classic fake pop-up (Microsoft/Windows, virus, "do not close", "call support now", a fictional 1-800-555-xxxx number): high
- same page where the number is absent but `has_tel_link=True`: high
- medium cases: alarm + action without phone (score 4); brand + alarm + action (score 6, no phone) is medium not high
- below threshold returns None: a recipe blog, "your account is locked" alone, a lone phone number, brand + payment (3), release notes mentioning Windows and Apple
- `fullscreen=True` lifts a score-2 page to medium (4)
- overlay bonus: the same signals passed as `overlay_text` score exactly +1 per matching category more than as body text
- high needs BOTH phone and alarm: a page with score >= 7 but no phone is medium; one with phone but no alarm is medium
- allowlist: FTC/Microsoft advice text on ftc.gov and support.microsoft.com gives `severity None` and `allowlisted True` while the same text on an unknown host is flagged; every trick host above is NOT allowlisted; `extra_allow=("examplebank.test",)` allowlists the bank and its subdomains
- plurals and word boundaries ("viruses", "gift cards" match; "pineapple", "windowsill" do not)
- normalization: fullwidth letters, zero-width characters inside a word, newlines and repeated spaces, mixed case
- no digits in `evidence` for any fixture, and `redact` removes phone runs (several formats) but leaves short numbers like "24" and "2026" alone
- determinism and purity: calling twice gives equal results; the module source contains no import from `ally`
- ONE test named `test_known_limitation_unknown_bank_help_page_scores_high`: a legitimate-looking help page on an unknown host ("If your account is locked, contact support at 1-800-555-0100.") currently scores high under the spec, and the same page with the bank passed in `extra_allow` is None. Add a comment that the user-bank allowlist is the mitigation.
- a news article about tech-support scams on a non-allowlisted host is at most medium

Run: `cd tests && python -m unittest test_risk_r1`. All must pass.

## Out of scope
Everything else: no Risk objects, no bridge/bus/agent/proactive, no extension, no R2-R5, no config files, no git commits (leave the changes uncommitted).
