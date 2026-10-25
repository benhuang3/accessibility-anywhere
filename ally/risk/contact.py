"""Tell a trusted person (a daughter, a neighbour) that Ally just stopped a likely scam.

Delivery is an ntfy.sh push notification: free, no account, the helper installs the ntfy app and subscribes to a
private topic. The message never contains the web address, page text or anything typed, only what kind of risk it was.
"""

import posixpath
import re
import urllib.parse
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
    return (f"Ally safety alert: {what}. Ally asked them to stop and wait. "
            "Please contact them now.")


# Video-call links Ally may open on the person's screen: a standing room the helper created in advance.
CALL_HOSTS = ("meet.google.com", "zoom.us", "whereby.com", "teams.microsoft.com", "teams.live.com", "meet.jit.si")


def _https_parts(url):
    if not isinstance(url, str) or re.search(r"[\s\x00-\x1f\x7f-\x9f\\]", url):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname
                or parts.username is not None or ":" in parts.netloc):
            return None
    except ValueError:
        return None
    return parts


def valid_spacecall_url(url) -> bool:
    return _https_parts(url) is not None and "#" not in url


def valid_call_url(url, spacecall_url: str = "") -> bool:
    parts = _https_parts(url)
    if parts is None:
        return False
    host = parts.hostname.lower()
    if len(url) <= 300 and any(host == h or host.endswith("." + h) for h in CALL_HOSTS):
        return True
    if not valid_spacecall_url(spacecall_url):
        return False
    base = urllib.parse.urlsplit(spacecall_url)
    path = posixpath.normpath(urllib.parse.unquote(parts.path) or "/")
    prefix = posixpath.normpath(urllib.parse.unquote(base.path) or "/").rstrip("/")
    return host == base.hostname.lower() and (path == prefix or path.startswith(prefix + "/"))


def valid_topic(topic) -> bool:
    return isinstance(topic, str) and bool(_TOPIC.match(topic))


def ntfy_sender(topic: str, server: str = "https://ntfy.sh", timeout: float = 4.0,
                spacecall_url: str = ""):
    """A send(text) -> bool function for one private topic, or None when the topic is not usable."""
    if not valid_topic(topic):
        return None
    url = f"{server.rstrip('/')}/{topic}"

    def send(text: str, join_url: str = "") -> bool:
        headers = {"Title": "Ally safety alert", "Priority": "high", "Tags": "warning"}
        if valid_call_url(join_url, spacecall_url):  # tapping the alert (or its button) joins the video call
            headers.update({"Click": join_url, "Actions": f"view, Join the call, {join_url}, clear=true"})
        try:
            req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    return send
