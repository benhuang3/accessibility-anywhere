"""Pure remote-tool start detection for risk rule R2, with an optional collector."""

from dataclasses import dataclass
import csv
import io
import json
from pathlib import Path
import re
import subprocess
import sys


# process names are UNVERIFIED, confirm on a Windows test PC
REMOTE_TOOLS: dict[str, tuple[str, ...]] = {
    "AnyDesk": ("anydesk",),
    "TeamViewer": ("teamviewer", "teamviewer_service", "teamviewer_desktop", "tv_w32", "tv_x64"),
    "UltraViewer": ("ultraviewer", "ultraviewer_desktop", "ultraviewer_service"),
    "Quick Assist": ("quickassist", "quick assist"),
    "ScreenConnect": ("screenconnect.clientservice", "screenconnect.windowsclient", "screenconnect"),
    "ConnectWise Control": ("connectwisecontrol.client",),
    "NetSupport": ("client32",),
    "RustDesk": ("rustdesk",),
    "Supremo": ("supremo", "supremoservice"),
    "Splashtop": ("splashtop", "srservice", "strwinclt"),
    "LogMeIn": ("logmein", "lmiguardian"),
    "AeroAdmin": ("aeroadmin",),
    "Ammyy": ("ammyy", "ammyyadmin"),
    "Zoho Assist": ("zohoassist",),
    "GetScreen": ("getscreen",),
}


def _normalize(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r"[ _-]+", "", name.strip().lower().removesuffix(".exe"))


_LOLRMM = Path(__file__).with_name("remote_tools.json")
# Never offer to close these: video meetings people use with family, and assistive technology (screen readers,
# magnifiers, dictation) that some LOLRMM entries reuse or that an older adult may depend on.
_NOT_REMOTE = frozenset({"zohomeeting", "nvda", "nvdaservice", "nvdaremote", "narrator", "magnify", "jfw", "zoomtext", "zt",
                         "dragon", "natspeak", "supernova", "fusion"})
_extended: dict[str, str] | None = None


def _lolrmm() -> dict[str, str]:
    """Normalized executable name -> tool name, from the LOLRMM catalogue (remote_tools.json, Apache-2.0).
    The hand-checked REMOTE_TOOLS above wins when both know a name."""
    global _extended
    if _extended is None:
        table = {}
        try:
            tools = json.loads(_LOLRMM.read_text(encoding="utf-8")).get("tools", {})
            for label, exes in tools.items():
                for exe in exes if isinstance(exes, list) else ():
                    key = _normalize(str(exe))
                    if key and key not in _NOT_REMOTE:
                        table.setdefault(key, str(label)[:60])
        except (OSError, ValueError, AttributeError):
            table = {}
        _extended = table
    return _extended


def match_remote_tool(name: str, original_filename: str = "") -> str | None:
    """Match exact normalized process names. `original_filename` (from the file's version info, when a caller
    has it) catches a renamed copy; the built-in snapshot only supplies the running name."""
    names = {_normalize(name), _normalize(original_filename)} - {""}
    for label, aliases in REMOTE_TOOLS.items():
        if any(_normalize(alias) in names for alias in aliases):
            return label
    extended = _lolrmm()
    return next((extended[n] for n in names if n in extended), None)


def known_tool(label: str) -> bool:
    """True for a tool name that match_remote_tool can return."""
    return label in REMOTE_TOOLS or label in set(_lolrmm().values())


@dataclass(frozen=True)
class RemoteEvent:
    tool: str
    process: str
    started: bool
    severity: str
    evidence: str


def _evidence(tool: str, started: bool, payment: bool, recent: bool) -> str:
    if not started:
        context = "may already be running"
    elif payment:
        context = "may have started while a payment or bank page is open"
    elif recent:
        context = "may have started soon after a scam-looking page"
    else:
        context = "may have just started"
    return f"{tool} {context}; please check whether you started it."


class RemoteWatcher:
    def __init__(self, high_window_s: float = 600.0):
        self.high_window_s = high_window_s
        self._previous: set[str] | None = None

    def observe(self, processes: list[dict], now: float, last_scam_text_at: float | None = None,
                payment_page_open: bool = False) -> list[RemoteEvent]:
        """Compare name snapshots using only caller-supplied context and time."""
        current = set()
        events = []
        recent = (last_scam_text_at is not None
                  and 0 <= now - last_scam_text_at <= self.high_window_s)
        for process in processes:
            if not isinstance(process, dict):
                continue
            name = process.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = _normalize(name)
            if key in current:
                continue
            current.add(key)
            tool = match_remote_tool(name, process.get("original_filename", ""))
            if tool is None or (self._previous is not None and key in self._previous):
                continue
            started = self._previous is not None
            high = payment_page_open or (started and recent)
            if any(event.tool == tool for event in events):
                continue  # one alert per tool per snapshot (TeamViewer.exe + TeamViewer_Service.exe)
            events.append(RemoteEvent(tool, name, started, "high" if high else "medium",
                                      _evidence(tool, started, payment_page_open, recent)))
        self._previous = current
        return events


def snapshot_processes() -> list[dict]:
    """Collect names best-effort; executable paths are not original-filename metadata."""
    try:
        import psutil
    except ImportError:
        try:
            if sys.platform != "win32":
                return []
            result = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                timeout=5, creationflags=subprocess.CREATE_NO_WINDOW, check=True)
            return [{"name": row[0]} for row in csv.reader(io.StringIO(result.stdout))
                    if row and row[0].strip()]
        except Exception:
            return []
    except Exception:
        return []
    names = []
    try:
        for process in psutil.process_iter(["name", "exe"]):
            try:
                name = process.info.get("name")
                if isinstance(name, str) and name.strip():
                    names.append({"name": name})
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
    except Exception:
        return []
    return names
