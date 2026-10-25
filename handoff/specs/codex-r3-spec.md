# Task: R3 lookalike-domain check as a pure function (Ally risk rules)

Read AGENTS.md and `ally/risk/text.py` (already in this worktree; match its style: small functions, frozen dataclass result, stdlib only). Python 3.11+. Work only in this worktree.

## Goal
Detect when a web address imitates a well-known organization (bank, Microsoft, PayPal, etc.) so Ally can warn an older adult calmly. PURE function, no network, no `ally` imports (no `ally.contracts`; a thin adapter is written elsewhere). It must have FEW false positives: a warning on a legitimate site ruins trust.

## Allowed files (touch nothing else)
- `ally/risk/domains.py` (new)
- `tests/test_risk_r3.py` (new)
Do NOT create `ally/risk/__init__.py` (implicit namespace package; verify `from ally.risk.domains import check_domain` imports).

## Public API (exactly these names)

```python
@dataclass(frozen=True)
class DomainCheck:
    severity: str | None        # "high", "medium" or None
    reasons: tuple[str, ...]    # short codes, see below
    evidence: str               # one plain sentence, no digits, never the raw URL
    host: str                   # normalized hostname ("" if unparseable)
    registrable: str            # registrable domain, e.g. "evil.co.uk"
    brand: str | None           # the brand being imitated, if any

BRANDS: dict[str, tuple[str, ...]]     # brand -> its real registrable domains
TOP_SITES: tuple[str, ...]             # small hand-written allowlist of very popular real sites
def host_of(url: str) -> str
def registrable_domain(host: str) -> str
def check_domain(url: str, extra_brands: dict[str, tuple[str, ...]] | None = None,
                 allow: tuple[str, ...] = ()) -> DomainCheck
```
`extra_brands` adds brands (for the user's own banks); `allow` is extra domains that are always clean (the real domain of a bank, or any site the user trusts). Both match the registrable domain and its subdomains.

## Behaviour

1. `host_of`: parse with `urllib.parse`; accept a missing scheme; lowercase; strip port, trailing dot, a leading `www.`; decode punycode labels (`xn--`) to Unicode with the `idna` codec (never raise: bad input returns ""); `host_of("https://paypal.com@evil.example/")` is `evil.example`.
2. `registrable_domain`: no external PSL library. Embed a small set of multi-part public suffixes (at least: co.uk, org.uk, ac.uk, gov.uk, com.au, net.au, org.au, co.nz, co.jp, co.in, co.za, com.br, com.mx, com.cn, com.sg, com.hk) and otherwise use the last two labels.
3. `BRANDS`, 25 to 30 entries, real domains only: paypal, chase, wellsfargo, bankofamerica, citi, usbank, capitalone, pnc, truist, americanexpress (americanexpress.com, amex.com), discover, venmo, zelle (zellepay.com), microsoft (microsoft.com, live.com, office.com, outlook.com, windows.com), apple (apple.com, icloud.com), google (google.com, gmail.com), amazon (amazon.com plus amazon.co.uk, .ca, .de, .in, .com.au, .co.jp, .fr, .it, .es), netflix, facebook (facebook.com, fb.com, meta.com), paypal also paypal.me, usps, ups, fedex, walmart, medicare (medicare.gov), ssa (ssa.gov), irs (irs.gov), coinbase. `TOP_SITES`: about 40 well-known real domains (wikipedia.org, youtube.com, nytimes.com, cnn.com, bbc.co.uk, ebay.com, etc.).
4. Never flag: the exact real domain of a brand or any subdomain of it; anything in `TOP_SITES`, in `allow`, or under them.
5. Matching is on LABELS of the registrable domain's name part (and on whole dot/hyphen tokens anywhere in the host), never on substrings inside longer words: `pineapple.com`, `chaseoutdoors.com`, `applebees.com`, `appleton.edu`, `chases.com`, `usa.gov`, `apply.com`, `case.edu`, `its.com` must all be clean.
6. Skeleton: lowercase, fold Cyrillic/Greek lookalikes to Latin (а е о р с у х і ј ѕ к м т в н ԁ ɡ and Greek α β ε ι κ ν ο ρ τ υ χ), digits 0->o, 1->l (also try 1->i), 3->e, 5->s, then `rn`->`m`, `vv`->`w`. Build the skeleton candidates and compare each to the brand names.
7. Reasons and severity:
   - `homoglyph` (HIGH): a label's skeleton equals a brand name but the label itself is not that brand (paypa1, micr0soft, rnicrosoft, Cyrillic аpple, gooogle is NOT this one).
   - `mixed_script` (HIGH): a single label mixes Latin with Cyrillic or Greek letters.
   - `punycode_lookalike` (HIGH): an `xn--` label whose decoded form folds to a brand.
   - `brand_subdomain` (HIGH): a brand token appears as a subdomain or middle label of an unrelated registrable domain (paypal.com.secure-login.xyz, chase.com-verify.net) AND the same host also has a trust keyword token (login, secure, verify, account, update, signin, support, billing, wallet); without the keyword it is `brand_token` (MEDIUM).
   - `brand_token` (MEDIUM): a whole token equal to a brand inside an unrelated domain (paypal-help.net).
   - `brand_keyword` (MEDIUM): the registrable name is brand + keyword or keyword + brand with a hyphen or none (paypalsecure.com, secure-chase.net).
   - `near_miss` (MEDIUM): edit distance (Damerau-Levenshtein) <= 1 from a brand name, or <= 2 when the brand name has 10 or more letters (americanexpress), and ONLY for brand names with 6 or more letters (so wellsfargo, paypal, microsoft qualify; irs, ssa, chase, apple, citi, ups, usps do not).
   - `raw_ip` (MEDIUM): the host is an IPv4 address (brand = None).
   - severity is the highest reason present; `brand` is set when a brand was matched.
8. `evidence`: one calm hedged sentence per top reason, generic, containing NO digits and NOT the raw URL (for example "This address looks similar to a well-known organization's website."). A different fixed sentence per reason.
9. `check_domain("")`, garbage, or a non-URL returns severity None with host "" and never raises.

## Tests (tests/test_risk_r3.py, unittest; mirror the header style of tests/test_risk_r1.py; run from the tests folder)
Positives (each: expected severity and reason): paypa1.com, micr0soft.com, rnicrosoft.com, a Cyrillic "аpple.com" (use а), a mixed-script label, an `xn--` lookalike you construct with the idna codec, paypal.com.secure-login.xyz (high, brand_subdomain), paypal-help.net (medium, brand_token), paypalsecure.com, secure-chase.net, paypall.com and wellsfargoo.com (near_miss), americanexpresss.com (distance rule for long names), http://192.168.1.10/login (raw_ip).
Clean (must be None): usa.gov, apply.com, case.edu, its.com, chases.com, pineapple.com, applebees.com, appleton.edu, chaseoutdoors.com, www.paypal.com, smile.amazon.com, www.amazon.co.uk, mail.google.com, accounts.google.com, en.wikipedia.org, https://login.microsoftonline.com? (NOT a brand entry; it must be clean because "microsoftonline" is a different whole token), localhost, an empty string.
Also: `https://paypal.com@evil.example/` is judged on evil.example (not flagged unless evil.example itself imitates); `allow=("examplebank.test",)` and `extra_brands={"examplebank": ("examplebank.test",)}` work together (examplebank-login.net flagged, examplebank.test and secure.examplebank.test clean); registrable_domain for bbc.co.uk, a.b.evil.com.au, example.com; no digits in any evidence; determinism; the module source has no import from `ally`.
Run `cd tests && python -m unittest test_risk_r3`. All must pass.

## Out of scope
Everything else: no Risk objects, no bridge/bus/agent, no extension, no other rules, no config, no git commits (leave changes uncommitted).
