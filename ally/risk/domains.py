"""Pure, standard-library lookalike-domain checks for risk rule R3."""

from dataclasses import dataclass
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DomainCheck:
    severity: str | None
    reasons: tuple[str, ...]
    evidence: str
    host: str
    registrable: str
    brand: str | None


BRANDS: dict[str, tuple[str, ...]] = {
    "paypal": ("paypal.com", "paypal.me"),
    "chase": ("chase.com",),
    "wellsfargo": ("wellsfargo.com",),
    "bankofamerica": ("bankofamerica.com",),
    "citi": ("citi.com",),
    "usbank": ("usbank.com",),
    "capitalone": ("capitalone.com",),
    "pnc": ("pnc.com",),
    "truist": ("truist.com",),
    "americanexpress": ("americanexpress.com", "amex.com"),
    "discover": ("discover.com",),
    "venmo": ("venmo.com",),
    "zelle": ("zellepay.com",),
    "microsoft": ("microsoft.com", "live.com", "office.com", "outlook.com", "windows.com"),
    "apple": ("apple.com", "icloud.com"),
    "google": ("google.com", "gmail.com", "googleblog.com", "googledomains.com", "youtube.com", "android.com"),
    "amazon": ("amazon.com", "amazon.co.uk", "amazon.ca", "amazon.de", "amazon.in",
               "amazon.com.au", "amazon.co.jp", "amazon.fr", "amazon.it", "amazon.es"),
    "netflix": ("netflix.com",),
    "facebook": ("facebook.com", "fb.com", "meta.com"),
    "usps": ("usps.com",),
    "ups": ("ups.com",),
    "fedex": ("fedex.com",),
    "walmart": ("walmart.com",),
    "medicare": ("medicare.gov",),
    "ssa": ("ssa.gov",),
    "irs": ("irs.gov",),
    "coinbase": ("coinbase.com",),
}

TOP_SITES: tuple[str, ...] = (
    "wikipedia.org", "youtube.com", "nytimes.com", "cnn.com", "bbc.co.uk", "bbc.com",
    "ebay.com", "reddit.com", "yahoo.com", "bing.com", "duckduckgo.com", "github.com",
    "stackoverflow.com", "linkedin.com", "instagram.com", "tiktok.com", "pinterest.com",
    "zoom.us", "adobe.com", "dropbox.com", "salesforce.com", "wordpress.com", "etsy.com",
    "target.com", "costco.com", "homedepot.com", "lowes.com", "bestbuy.com", "weather.com",
    "espn.com", "npr.org", "pbs.org", "reuters.com", "apnews.com", "theguardian.com",
    "nih.gov", "cdc.gov", "usa.gov", "aarp.org", "microsoftonline.com",
)

_SUFFIXES = frozenset(("co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
                       "co.nz", "co.jp", "co.in", "co.za", "com.br", "com.mx", "com.cn",
                       "com.sg", "com.hk"))
# Global brands run their own country sites (google.co.uk, paypal.de, amazon.nl). Only these country suffixes
# count, never "any two-letter TLD": free ones such as .tk, .cc and .ml are where lookalikes get registered.
_REGIONAL_BRANDS = frozenset(("google", "paypal", "amazon", "microsoft", "apple", "netflix", "facebook",
                              "walmart", "ebay"))
_REGIONAL_SUFFIXES = frozenset((
    "co.uk", "de", "fr", "ca", "in", "es", "it", "nl", "se", "pl", "ie", "ch", "at", "be", "dk", "no", "fi",
    "pt", "jp", "co.jp", "com.au", "com.br", "com.mx", "co.nz", "com.sg", "co.za", "com.hk", "com.tr", "com.ar",
    "co.kr", "com.tw", "co.il", "ae", "sa", "cn", "com.cn"))
_KEYWORDS = frozenset(("login", "secure", "verify", "account", "update", "signin", "support",
                       "billing", "wallet"))
_FOLD = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s", "к": "k", "м": "m", "т": "t", "в": "b",
    "н": "h", "ӏ": "l", "ɡ": "g", "α": "a", "β": "b", "ε": "e", "ι": "i",
    "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "τ": "t", "υ": "y", "χ": "x",
})
_DIGITS = str.maketrans("0135", "oles")
_EVIDENCE = {
    "punycode_lookalike": "This encoded address may resemble a familiar organization's website.",
    "homoglyph": "This address uses characters that may resemble a familiar organization's name.",
    "mixed_script": "This address mixes alphabets in a way that may be misleading.",
    "brand_subdomain": "This address pairs a familiar name with a sign in or account related word on another domain.",
    "brand_token": "This address includes a familiar organization's name on another domain.",
    "brand_keyword": "This address combines a familiar name with an account related word.",
    "near_miss": "This address looks similar to a well known organization's website.",
    "raw_ip": "This address uses a numeric location instead of a website name, which may warrant care.",
}
_HIGH = frozenset(("punycode_lookalike", "homoglyph", "mixed_script", "brand_subdomain"))


def host_of(url: str) -> str:
    """Parse a web address, rejecting malformed hosts and non web schemes."""
    try:
        value = url.strip()
        if re.match(r"^(?:mailto|file|data|javascript|ftp):", value, re.IGNORECASE):
            return ""
        parsed = urlsplit(value if "://" in value or value.startswith("//") else "//" + value)
        if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
            return ""
        parsed.port  # Validate malformed ports as well as malformed brackets.
        host = (parsed.hostname or "").lower().rstrip(".")
        labels = [label.encode("ascii").decode("idna") if label.startswith("xn--") else label
                  for label in host.split(".")]
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               or any(not (c.isalnum() or c == "-") for c in label) for label in labels):
            return ""
        host = ".".join(labels).lower()
        if len(host) > 253 or ("." not in host and host != "localhost"):
            return ""
        return host[4:] if host.startswith("www.") else host
    except (ValueError, UnicodeError, AttributeError, TypeError):
        return ""


def registrable_domain(host: str) -> str:
    """Use a deliberately small public suffix table; this is not a full PSL."""
    host = host_of(host)
    if not host:
        return ""
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        pass
    labels = host.split(".")
    count = 3 if ".".join(labels[-2:]) in _SUFFIXES else 2
    return ".".join(labels[-count:])


def _skeletons(label: str) -> set[str]:
    folded = label.lower().translate(_FOLD)
    return {folded.replace("1", one).translate(_DIGITS).replace("rn", "m").replace("vv", "w")
            for one in ("l", "i")}


def _mixed(label: str) -> bool:
    scripts = {script for c in label for script in ("LATIN", "CYRILLIC", "GREEK")
               if script in unicodedata.name(c, "")}
    return "LATIN" in scripts and bool(scripts & {"CYRILLIC", "GREEK"})


def _distance(left: str, right: str) -> int:
    """Damerau-Levenshtein distance, including adjacent transpositions."""
    # Track last occurrences so repeated or overlapping edits remain valid.
    size = len(left) + len(right)
    table = [[0] * (len(right) + 2) for _ in range(len(left) + 2)]
    table[0][0] = size
    for i in range(len(left) + 1):
        table[i + 1][0], table[i + 1][1] = size, i
    for j in range(len(right) + 1):
        table[0][j + 1], table[1][j + 1] = size, j
    seen = {}
    for i, a in enumerate(left, 1):
        match = 0
        for j, b in enumerate(right, 1):
            row, column = seen.get(b, 0), match
            cost = int(a != b)
            if not cost:
                match = j
            table[i + 1][j + 1] = min(table[i][j] + cost, table[i + 1][j] + 1,
                                      table[i][j + 1] + 1,
                                      table[row][column] + i - row + j - column - 1)
        seen[a] = i
    return table[-1][-1]


def _tld(host: str) -> str:
    return host.rsplit(".", 1)[-1]


def check_domain(url: str, extra_brands: dict[str, tuple[str, ...]] | None = None,
                 allow: tuple[str, ...] = ()) -> DomainCheck:
    """Return deterministic local signals, with trusted domains taking precedence."""
    host = host_of(url)
    registrable = registrable_domain(host)
    clean = DomainCheck(None, (), "No unusual address signals were found.", host, registrable, None)
    if not host or "." not in host:
        return clean
    brands = dict(BRANDS)
    for name, domains in (extra_brands or {}).items():
        key = name.lower()
        brands[key] = brands.get(key, ()) + tuple(domains)
    trusted = TOP_SITES + allow + tuple(domain for domains in brands.values() for domain in domains)
    if any(host == domain or host.endswith("." + domain)
           for domain in (registrable_domain(item) for item in trusted) if domain):
        return clean
    # Brand top-level domains (m365.cloud.microsoft, share.google) can only be registered by the brand itself.
    if _tld(host) in brands:
        return clean
    own_name = registrable.split(".")[0]
    if own_name in _REGIONAL_BRANDS and own_name in brands and registrable[len(own_name) + 1:] in _REGIONAL_SUFFIXES:
        return clean
    try:
        ipaddress.IPv4Address(host)
        return DomainCheck("medium", ("raw_ip",), _EVIDENCE["raw_ip"], host, registrable, None)
    except ValueError:
        pass
    labels = host.split(".")
    tokens = re.split(r"[.-]", host)
    name = registrable.split(".")[0]
    hits = {}
    if any(_mixed(label) for label in labels):
        hits["mixed_script"] = None
    for brand in sorted(brands):
        for label in labels:
            if label != brand and brand in _skeletons(label):
                hits.setdefault("homoglyph", brand)
                # IDNA encoding is canonical, so compare the original parsed labels.
                raw_host = (urlsplit(url if "://" in url or url.startswith("//") else "//" + url).hostname or "").lower()
                if label.encode("idna").decode("ascii") in raw_host.split(".") and not label.isascii():
                    hits.setdefault("punycode_lookalike", brand)
        if brand in tokens:
            subdomains = labels[:-len(registrable.split("."))]
            middle = brand in re.split(r"[.-]", ".".join(subdomains))
            reason = "brand_subdomain" if middle and _KEYWORDS.intersection(tokens) else "brand_token"
            hits.setdefault(reason, brand)
        if any(name in (brand + word, word + brand, brand + "-" + word, word + "-" + brand)
               for word in _KEYWORDS):
            hits.setdefault("brand_keyword", brand)
        if len(brand) >= 6 and name != brand and abs(len(name) - len(brand)) <= (2 if len(brand) >= 10 else 1):
            if _distance(name, brand) <= (2 if len(brand) >= 10 else 1):
                hits.setdefault("near_miss", brand)
    reasons = tuple(reason for reason in _EVIDENCE if reason in hits)
    if not reasons:
        return clean
    matched = next((hits[reason] for reason in reasons if hits[reason] is not None), None)
    return DomainCheck("high" if _HIGH.intersection(reasons) else "medium", reasons,
                       _EVIDENCE[reasons[0]], host, registrable, matched)
