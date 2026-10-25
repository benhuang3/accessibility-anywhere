"""Detect system tools newly opened while a remote-control tool is running."""


# Warning priority: prefer a specific tool over the generic system console.
SCARE_TOOLS: dict[str, str] = {
    "eventvwr.exe": "Event Viewer",
    "tree.com": "a scrolling file list",
    "netstat.exe": "a list of network connections",
    "msconfig.exe": "System Configuration",
    "msinfo32.exe": "System Information",
    "regedit.exe": "the Registry Editor",
    "mmc.exe": "Event Viewer or another system console",
}


class FakeScanWatcher:
    def __init__(self):
        self._previous: set[str] | None = None

    def observe(self, names: list[str], remote_tools: tuple[str, ...], now: float) -> list[str]:
        """Return new scare-tool names after the remote session's first snapshot."""
        if not remote_tools:
            self._previous = None
            return []
        current = {name.strip().lower() for name in names if isinstance(name, str)} & SCARE_TOOLS.keys()
        appeared = sorted(current - self._previous) if self._previous is not None else []
        self._previous = current
        return appeared
