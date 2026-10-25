# Task: harden Ally's risk rules (ally/risk/)

Read AGENTS.md and ally/contracts.py (class Risk, RISK_KINDS, SEVERITIES) first. Python 3.11+, stdlib only, no new dependencies.

## Goal
Replace the starter heuristics in `ally/risk/__init__.py` with a small package that detects, for older adults:
lookalike domains, scam phrases, remote-access tools, payment fields on risky pages. Calm, hedged wording; NEVER output a phone number anywhere.

## Allowed files (touch nothing else)
- ally/risk/__init__.py, ally/risk/domains.py, ally/risk/text.py, ally/risk/process.py, ally/risk/fields.py, ally/risk/wire.py
- tests/test_risk.py (new)

## Public API that MUST keep working (tests/test_proactive.py::test_risk_rules already uses it)
`check_text(text, url="") -> list[Risk]`, `check_url(url) -> list[Risk]`, `check_process_names(names) -> list[Risk]`,
`warning_for(risk) -> (text, spoken)` (text must still contain "Why:" when evidence is set), constants `WARNINGS`.
Existing expectations: scam text "WARNING! Your computer has been locked. Call Microsoft Support" -> first Risk kind "scam_popup"; "Welcome to the recipe blog" -> []; https://paypal-secure-login.xyz/a -> untrusted_site; https://www.paypal.com/signin -> []; https://anydesk.com/download -> remote_access.

## New API
`scan_page(url, text, fields=None) -> list[Risk]` combining check_url + check_text + fields; `fields` is {"payment": bool, "password": bool, "http": bool}.
`Deduper(window_seconds=1800, clock=time.time)` with `.fresh(risk) -> bool`: True the first time per (host-of-risk.url or risk.app, kind) within the window.
`redact(s) -> str`: removes any run of 7+ digits (allowing spaces, dashes, dots, parentheses, leading +) and replaces with "a phone number". Apply to every Risk.evidence you produce.

## domains.py
- Normalize host: lowercase, decode xn-- (IDNA), strip "www.", registrable domain = last two labels, or last three when the second-level is in a short set (co, com, org, gov, net, ac) with a 2-letter TLD.
- Skeleton: fold Cyrillic/Greek lookalikes to Latin, 0->o 1->l 3->e 5->s, "rn"->"m", "vv"->"w".
- BRANDS maps brand -> real registrable domains: paypal(paypal.com), chase(chase.com), wellsfargo(wellsfargo.com), bankofamerica(bankofamerica.com), amazon(amazon.com), apple(apple.com, icloud.com), microsoft(microsoft.com, live.com, office.com), irs(irs.gov), medicare(medicare.gov), ssa(ssa.gov). The real domain and any subdomain of it are ALWAYS clean.
- high: skeleton of a label equals a brand but the domain isn't real (paypa1.com, rnicrosoft.com, Cyrillic "аpple.com"), or a single label mixes scripts.
- medium: Damerau-Levenshtein <=1 from a brand (<=2 if brand >=8 chars) e.g. chasse.com; OR brand is a whole token split on "-" or "." anywhere in the host outside the real domain (paypal-verify.net, paypal.com.secure-login.xyz); OR host is a raw IPv4.
- Must NOT trigger: pineapple.com, chaseoutdoors.com, smile.amazon.com, applebees.com is acceptable to flag only if you can justify; prefer not flagging. Add tests for the clean cases.
- Remote-access hosts (anydesk, teamviewer, ...) stay remote_access/high as today.

## text.py
Normalize: NFKC, strip zero-width chars, collapse whitespace, lowercase. Categories: tech_support (call/contact microsoft|apple|windows|tech support), locked_infected (computer infected/locked/blocked, virus detected), do_not_close, payment_demand (gift cards, wire transfer, bitcoin ATM, crypto), account_suspended, gov_impersonation (irs, medicare, social security + arrest/owe/suspend), urgency (immediately, within 24 hours, final notice). A phone-number regex is a signal only.
Severity: high if (tech_support or payment_demand) AND (phone number present or >=2 categories), or >=3 categories; medium if exactly 2 categories; one soft match -> []. Return one Risk kind "scam_popup" with evidence naming the categories in plain words (no raw page text, no digits runs).

## process.py
REMOTE_TOOLS add: rustdesk, splashtop, aeroadmin, ammyy, zohoassist, getscreen. check_process_names ignores case, spaces and ".exe". `running_process_names() -> list[str]` runs `tasklist /fo csv /nh` on win32 (subprocess, timeout 5s, creationflags CREATE_NO_WINDOW) and returns [] on any other platform or any error.

## fields.py
`check_fields(url, fields, other_risks) -> list[Risk]`: payment_field/medium only when fields.payment AND (fields.http OR other_risks non-empty). Otherwise [].

## __init__.py
Re-export the API above. Rewrite WARNINGS to be calmer and hedged ("This might be", "Please check"), never imperative-scary, no phone numbers. Add `BANNERS` dict kind -> one short sentence for an in-page banner.

## wire.py
`attach(agent)`: duck-typed, MUST NOT import agent or bridge modules. Uses agent.bridge (attribute on_page set to a function taking the "page" event dict {url,title,text,fields}), agent.bus (publish/subscribe), agent.events if present.
- on_page: scan_page -> Deduper.fresh -> bus.publish("risk", risk) for each fresh one.
- subscribe "risk_warning": call agent.bridge.broadcast("showRiskBanner", {"url": risk url, "kind": kind, "text": BANNERS[kind]}) for risks whose url is non-empty.
- start a daemon thread (10s interval, stoppable) that publishes a remote_access Risk once per newly seen tool from running_process_names().
- return an object with .stop().

## Acceptance tests (tests/test_risk.py, unittest, ~30 cases)
lookalikes (paypa1, rnicrosoft, Cyrillic apple, chasse, paypal-verify.net, IP), clean (pineapple.com, www.paypal.com, smile.amazon.com, chaseoutdoors.com, recipe blog), phrase scoring (one soft match -> [], tech support + phone -> high), redaction (assert no run of 7+ digits in any Risk.evidence or warning_for output across all fixtures), Deduper window with injected clock, fields policy, process names (".EXE", mixed case, spaces), scan_page end to end, wire.attach with fake agent/bridge/bus.
Run: `cd tests && python -m unittest test_risk test_proactive` — both must pass. Do not modify test_proactive.py.

## Out of scope
Anything outside the allowed files; the extension; bridge/agent/proactive changes; git commits (leave changes uncommitted).
