"""Tiny Claude Messages API client using only the standard library.

POST {base_url}/v1/messages with headers x-api-key and anthropic-version.
Retries once on overload/5xx/429 and on network errors. Raises ClaudeError otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.request

log = logging.getLogger("ally.claude")

RETRY_STATUS = {408, 429, 500, 502, 503, 504, 529}


class ClaudeError(Exception):
    def __init__(self, message: str, status: int | None = None, kind: str = "", offline: bool = False):
        super().__init__(message)
        self.status = status
        self.kind = kind
        self.offline = offline  # network problem (no internet, DNS, timeout)


def ssl_context() -> ssl.SSLContext:
    """Use certifi's CA bundle when installed (fixes certificate errors on some Python installs)."""
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class Claude:
    def __init__(self, api_key: str | None = None, api_version: str = "2023-06-01",
                 base_url: str | None = None, timeout: float = 20.0, retries: int = 1):
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")
        self.api_version = api_version
        self.base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self._ctx = ssl_context()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        if not self.api_key:
            raise ClaudeError("No ANTHROPIC_API_KEY set.", kind="no_key", offline=True)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "content-type": "application/json",
            "accept": "application/json",
        }
        attempt = 0
        while True:
            attempt += 1
            req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
            try:
                kwargs = {"timeout": timeout or self.timeout}
                if self.base_url.startswith("https://"):
                    kwargs["context"] = self._ctx
                with urllib.request.urlopen(req, **kwargs) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", "replace") if e.fp else ""
                kind, msg = "http_error", raw[:500] or str(e)
                try:
                    err = json.loads(raw).get("error", {})
                    kind, msg = err.get("type", kind), err.get("message", msg)
                except (ValueError, AttributeError):
                    pass
                if e.code in RETRY_STATUS and attempt <= self.retries:
                    wait = 1.0
                    try:
                        wait = min(float(e.headers.get("retry-after", "1")), 4.0)
                    except (TypeError, ValueError):
                        pass
                    log.warning("Claude API %s (%s), retrying in %.1fs", e.code, kind, wait)
                    time.sleep(wait)
                    continue
                raise ClaudeError(f"Claude API error {e.code} ({kind}): {msg}", status=e.code, kind=kind)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if attempt <= self.retries:
                    time.sleep(0.5)
                    continue
                reason = getattr(e, "reason", e)
                raise ClaudeError(f"Can't reach the Claude API: {reason}", kind="network", offline=True)
            except ValueError as e:
                raise ClaudeError(f"Bad response from the Claude API: {e}", kind="bad_response")

    def messages(self, **body) -> dict:
        return self._request("POST", "/v1/messages", body)

    def ping(self, timeout: float = 6.0) -> str:
        """Cheap reachability + key check (lists one model; uses no tokens)."""
        out = self._request("GET", "/v1/models?limit=1", None, timeout=timeout)
        models = out.get("data") or []
        return models[0].get("id", "ok") if models else "ok"


def text_of(response: dict) -> str:
    return "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text").strip()
