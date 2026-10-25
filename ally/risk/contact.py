"""Tell a trusted person (a daughter, a neighbour) that Ally just stopped a likely scam.

Delivery is an ntfy.sh push notification: free, no account, the helper installs the ntfy app and subscribes to a
private topic. The message never contains the web address, page text or anything typed, only what kind of risk it was.
"""

import re
import urllib.request

_TOPIC = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_WHAT = {
    "remote_access": "someone may be controlling the computer while a bank or card page is open",
    "scam_popup": "a fake warning page that tries to get people to call a number",
    "untrusted_site": "a website pretending to be one they use",
    "payment_field": "a page asking for card details on an unfamiliar site",
}


def message_for(kind: str) -> str:
    what = _WHAT.get(kind, "a page that looks like a scam")
    return (f"Ally safety alert: {what}. Ally asked them to pause and nothing has been paid. "
            "A quick call from you would help.")


def valid_topic(topic) -> bool:
    return isinstance(topic, str) and bool(_TOPIC.match(topic))


def ntfy_sender(topic: str, server: str = "https://ntfy.sh", timeout: float = 4.0):
    """A send(text) -> bool function for one private topic, or None when the topic is not usable."""
    if not valid_topic(topic):
        return None
    url = f"{server.rstrip('/')}/{topic}"

    def send(text: str) -> bool:
        req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST",
                                     headers={"Title": "Ally safety alert", "Priority": "high", "Tags": "warning"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    return send
