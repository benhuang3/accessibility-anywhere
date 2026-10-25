"""Plain warning wording for the proactive coordinator."""


_GUARDIAN_TITLES = {"high": "This looks like a scam.", "medium": "Please be careful with this page."}


def warning_for(risk) -> tuple[str, str]:
    try:
        if risk.source == "risk.guardian" and risk.evidence:
            # The vision model's own plain-language explanation, already redacted and length-capped.
            title = _GUARDIAN_TITLES.get(risk.severity, _GUARDIAN_TITLES["medium"])
            return f"{title} {risk.evidence}", risk.evidence
        from . import copy as warning_copy
        from . import domains

        usual_site = ""
        if risk.kind == "untrusted_site":
            brand = domains.check_domain(risk.url).brand
            usual_site = domains.BRANDS.get(brand, ("",))[0]
        key = warning_copy.copy_key(
            risk.kind, usual_site, risk.kind == "remote_access" and risk.source == "risk.joined")
        wording = warning_copy.render(key, tool=risk.app, host=domains.host_of(risk.url),
                                      usual_site=usual_site)
        return wording.title + " " + wording.body, wording.spoken
    except Exception:
        safe = "This page may not be safe. Please be careful."
        return safe, safe
