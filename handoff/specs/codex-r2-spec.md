# Task: R2 remote-access tool watcher as pure logic (Ally risk rules)

Read AGENTS.md and `ally/risk/text.py` (already in this worktree; match its style: small functions, frozen dataclass results, stdlib only). Python 3.11+. Work only in this worktree.

## Goal
Scammers talk victims into running remote-control tools (AnyDesk, TeamViewer, Quick Assist ...). Detect when such a tool STARTS and how risky that is. The watcher logic must be pure and testable: the process list and the clock are passed in. Only a thin collector touches the system. No `ally` imports (no `ally.contracts`, no bus/bridge), no network.

## Allowed files (touch nothing else)
- `ally/risk/process.py` (new)
- `tests/test_risk_r2.py` (new)
Do NOT create `ally/risk/__init__.py` (implicit namespace package; verify `from ally.risk.process import match_remote_tool` imports).

## Public API (exactly these names)

```python
REMOTE_TOOLS: dict[str, tuple[str, ...]]   # label -> process names (lowercase, no ".exe")

def match_remote_tool(name: str, original_filename: str = "") -> str | None   # returns the tool label

@dataclass(frozen=True)
class RemoteEvent:
    tool: str                 # label, e.g. "AnyDesk"
    process: str              # the process name seen
    started: bool             # True if it appeared after the first snapshot; False if it was already running then
    severity: str             # "medium" or "high"
    evidence: str             # one plain sentence, no digits

class RemoteWatcher:
    def __init__(self, high_window_s: float = 600.0)
    def observe(self, processes: list[dict], now: float, last_scam_text_at: float | None = None,
                payment_page_open: bool = False) -> list[RemoteEvent]
    # processes: dicts with "name" (required) and optional "original_filename"

def snapshot_processes() -> list[dict]     # thin collector, see below
```

## match_remote_tool
Lowercase, strip surrounding spaces, remove a trailing `.exe`, treat spaces/underscores/hyphens as equivalent. Match the NAME, or `original_filename` when given (portable copies get renamed, so a renamed binary with the right original filename still matches). `REMOTE_TOOLS` must include (data-driven, easy to edit, with a comment saying "process names are UNVERIFIED, confirm on a Windows test PC"): AnyDesk (anydesk), TeamViewer (teamviewer, teamviewer_service, teamviewer_desktop, tv_w32, tv_x64), UltraViewer (ultraviewer, ultraviewer_desktop, ultraviewer_service), Quick Assist (quickassist, quick assist), ScreenConnect (screenconnect.clientservice, screenconnect.windowsclient, screenconnect), ConnectWise Control (connectwisecontrol.client), NetSupport (client32), RustDesk (rustdesk), Supremo (supremo, supremoservice), Splashtop (splashtop, srservice, strwinclt), LogMeIn (logmein, lmiguardian), AeroAdmin (aeroadmin), Ammyy (ammyy, ammyyadmin), Zoho Assist (zohoassist, zohomeeting), GetScreen (getscreen). Matching is EXACT on the normalized name or on one of these entries (not a substring), so "anydeskhelper", "teamviewerfan", "client3200" and ordinary names like "chrome", "python", "svchost", "explorer" do NOT match. "client32" must match only exactly.

## RemoteWatcher.observe
- The FIRST call is the baseline: tools already running then are reported once, `started=False`.
- Later calls report a tool only when it newly appears (a process name not in the previous snapshot); `started=True`. A tool that stays running is not reported again; if it exits and reappears it is reported again as started.
- Severity: "medium" by default. "high" when `started` is True AND (`payment_page_open` is True OR `last_scam_text_at` is not None and `0 <= now - last_scam_text_at <= high_window_s`). A tool that was already running at the baseline is also "high" when `payment_page_open` is True, otherwise "medium".
- Evidence: one calm hedged sentence by case (started / already running / started while a payment or bank page is open / started soon after a scam-looking page); a fixed sentence per case; no digits; mention the tool label but not paths. Never call the tool malware: they are legitimate tools (the wording asks whether the user started it).
- Must not raise on malformed process dicts (missing name, None, wrong types): skip them.
- Pure and deterministic: no clock reads, no I/O inside `observe`.

## snapshot_processes
Returns `[{"name": ..., "original_filename": ...?}]`. Try `import psutil` inside the function (optional dependency): iterate `psutil.process_iter(["name", "exe"])`, tolerate AccessDenied/NoSuchProcess. If psutil is missing, on win32 fall back to `tasklist /fo csv /nh` via subprocess (timeout 5s, `creationflags=subprocess.CREATE_NO_WINDOW`); on any other platform or any error return []. Never raise.

## Tests (tests/test_risk_r2.py, unittest; mirror the header style of tests/test_risk_r1.py; run from the tests folder)
- match_remote_tool: "AnyDesk.exe", "ANYDESK", "TeamViewer_Service.exe", "Quick Assist", "QuickAssist.exe", "rustdesk.exe", "Supremo.exe", "client32.exe", renamed binary with `original_filename="AnyDesk.exe"`, "ScreenConnect.ClientService.exe" match; "anydeskhelper", "teamviewerfan", "client3200", "chrome", "python", "svchost", "explorer", "", None-like junk do not
- watcher baseline: tool already running at first call -> one event, started False, medium; second identical call -> no events
- new tool on a later call -> started True, medium; stays running -> not repeated; exits and returns -> reported again
- high when payment_page_open; high when a scam text was seen within the window (and medium just outside it, and with a future timestamp); baseline-present tool is high only with payment_page_open
- junk process entries never raise
- no digits in any evidence; determinism (same inputs, same outputs, with a fresh watcher); the module source has no import from `ally` at module level
- `snapshot_processes` with `psutil` patched via `unittest.mock` (process_iter returning fake processes, one raising AccessDenied) returns names; with psutil import failing and platform not win32 returns []; never raises. Do NOT launch real tasklist in tests.
Run `cd tests && python -m unittest test_risk_r2`. All must pass.

## Out of scope
Everything else: no Risk objects, no bridge/bus/agent, no extension, no killing or closing processes, no other rules, no config, no git commits (leave changes uncommitted).
