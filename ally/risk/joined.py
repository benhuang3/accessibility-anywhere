"""Joined rule: a remote-control tool is running while a bank or payment page is open.

This is the strongest scam signal (the scammer asks the victim to log in to their bank "to get a refund"
while watching the screen), and the one a browser-only tool cannot see. Pure: the caller passes what is
running and which page is open.
"""

from dataclasses import dataclass

from .domains import BRANDS, check_domain, host_of, registrable_domain
from .process import match_remote_tool

BANK_BRANDS = frozenset((
    "chase", "wellsfargo", "bankofamerica", "citi", "usbank", "capitalone", "pnc", "truist", "americanexpress",
    "discover", "paypal", "venmo", "zelle", "coinbase",
))

_EVIDENCE = {
    "bank_page": "A remote-control tool may be running while a bank page is open.",
    "lookalike_bank_page": "A remote-control tool may be running while a page that looks like a bank is open.",
    "card_page": "A remote-control tool may be running while card details are being asked for.",
}


@dataclass(frozen=True)
class JoinedRisk:
    severity: str                 # always "high"
    reasons: tuple[str, ...]      # "remote_tool" plus one of bank_page, lookalike_bank_page, card_page
    evidence: str                 # one plain sentence, no digits, no URL
    tools: tuple[str, ...]        # remote tool labels that are running
    host: str


def running_tools(processes: list[dict]) -> tuple[str, ...]:
    """Remote tool labels among a process snapshot ([{"name": ...}, ...]); junk entries are ignored."""
    labels = set()
    for process in processes if isinstance(processes, (list, tuple)) else ():
        if isinstance(process, dict):
            label = match_remote_tool(process.get("name"), process.get("original_filename", ""))
            if label:
                labels.add(label)
    return tuple(sorted(labels))


def _owned_by(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in (registrable_domain(x) for x in domains) if d)


def joined_risk(remote_tools, url: str, card_present: bool = False,
                extra_banks: dict[str, tuple[str, ...]] | None = None) -> JoinedRisk | None:
    """High when any remote tool is running and the open page is a bank, a lookalike of one, or asks for card
    details. `extra_banks` adds the user's own banks ({"examplebank": ("examplebank.test",)})."""
    tools = tuple(sorted({str(t) for t in remote_tools or () if t}))
    if not tools:
        return None
    host = host_of(url if isinstance(url, str) else "")
    banks = {name: BRANDS[name] for name in BANK_BRANDS if name in BRANDS}
    banks.update({name.lower(): tuple(domains) for name, domains in (extra_banks or {}).items()})
    if host and any(_owned_by(host, domains) for domains in banks.values()):
        kind = "bank_page"
    elif host and check_domain(url, extra_brands=extra_banks).brand in banks:
        kind = "lookalike_bank_page"
    elif card_present:
        kind = "card_page"
    else:
        return None
    return JoinedRisk("high", ("remote_tool", kind), _EVIDENCE[kind], tools, host)
