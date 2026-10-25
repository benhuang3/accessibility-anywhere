"""Risk actions, with external effects supplied by the caller."""

from dataclasses import dataclass
import subprocess
import sys

from . import process, text


def ask_for(risk) -> tuple[str, str] | None:
    if risk.kind in ("scam_popup", "untrusted_site", "payment_field"):
        return "Want me to close this page?", "close_page"
    if risk.kind == "remote_access":
        tool = text.redact(risk.app or "the remote-control tool")[:40]
        return f"Want me to close {tool}?", "close_tool"
    return None


def close_page(call, url, tab_id=None) -> bool:
    try:
        return call("closeTab", {"url": url, "tabId": tab_id}).get("closed", 0) > 0
    except Exception:
        return False


@dataclass(frozen=True)
class KillResult:
    ok: bool
    access_denied: bool = False


def _kill_result(name) -> KillResult:
    if sys.platform != "win32":
        return KillResult(False)
    result = subprocess.run(["taskkill", "/IM", name, "/F"],
                            capture_output=True, timeout=10)
    output = " ".join(str(getattr(result, stream, "") or "")
                      for stream in ("stdout", "stderr")).lower()
    return KillResult(result.returncode == 0,
                      result.returncode == 1 and "access is denied" in output)


def _kill(name) -> bool:
    return _kill_result(name).ok


def close_tool(tool_label, snapshot=process.snapshot_processes, kill=_kill) -> int:
    count = 0
    try:
        if not process.known_tool(tool_label):
            return 0
        names = set()
        for item in snapshot():
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if process.match_remote_tool(name) == tool_label:
                names.add(name)
        for name in names:
            try:
                # Recording callables may return None; explicit False means failure.
                if kill(name) is not False:
                    count += 1
            except Exception:
                pass
    except Exception:
        pass
    return count


def perform(action_id, risk, call, snapshot=process.snapshot_processes, kill=_kill, tab_id=None) -> bool:
    try:
        if action_id == "close_page":
            return close_page(call, risk.url, tab_id)
        if action_id == "close_tool":
            # A joined warning can name several tools ("AnyDesk, TeamViewer"): close each one.
            labels = [label.strip() for label in (risk.app or "").split(",") if label.strip()]
            return sum(close_tool(label, snapshot, kill) for label in labels) > 0
    except Exception:
        pass
    return False
