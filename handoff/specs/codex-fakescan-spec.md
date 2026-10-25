# Codex spec: catch the "fake scan" trick during a remote session

Worktree `wt-risk-all`, branch `risk-branch`.
- Do not commit, push, merge or switch branches.
- Never read `.env` or `config.toml`.
- Do not start `run.py`.
- Do not launch or kill real processes; tests use fake snapshots.

## Why
Tech-support scammers connect with a remote tool (AnyDesk, Quick Assist and so on), then open built-in Windows tools to "prove" the PC
is infected:
- Event Viewer full of normal warnings;
- `tree` or `dir /s` scrolling past;
- `netstat` showing "hackers";
- msconfig.

That's the moment before they ask for money. Ally already knows when a remote tool is running. Warn when one of these tools *newly
starts* while a remote tool is connected.

Read first:
- `ally/risk/process.py` (`snapshot_processes` returns names only, from psutil or `tasklist`; `RemoteWatcher`; `known_tool`)
- `ally/risk/joined.py` (`running_tools`)
- `ally/risk/wire.py` (`poll_once`, `Deduper`, `on_risk_warning`, the cooldown and confirm flow, `_perform_action`)
- `ally/risk/__init__.py` (`warning_for`, with its source-based branches)
- `ally/risk/copy.toml`, `ally/risk/copy.py`
- `ally/contracts.py` (`Risk` kinds are fixed: reuse `remote_access`, and use a new `source`)
- `tests/test_risk_r2.py` and `tests/test_risk_wire.py`

## Allowed files
- `ally/risk/fakescan.py` (new)
- `ally/risk/wire.py`, `ally/risk/__init__.py`, `ally/risk/copy.toml`, `ally/risk/actions.py` (only if needed)
- `tests/test_risk_fakescan.py` (new)
- `docs/RISK_HANDOFF.md` (a short subsection) and `README.md` (one line in the Scam protection section)

## Contract
- **`fakescan.py`:**
  - `SCARE_TOOLS`: a mapping from lowercase process names to a plain description. At least:

    | Process name | Description |
    |---|---|
    | `eventvwr.exe` | Event Viewer |
    | `mmc.exe` | Event Viewer or another system console |
    | `tree.com` | a scrolling file list |
    | `netstat.exe` | a list of network connections |
    | `msconfig.exe` | System Configuration |
    | `msinfo32.exe` | System Information |
    | `regedit.exe` | the Registry Editor |

    Do **not** include cmd.exe, powershell.exe, conhost.exe, tasklist.exe or taskkill.exe. They're too common, and Ally itself runs
    tasklist and taskkill.
  - `class FakeScanWatcher` with `observe(names, remote_tools, now) -> list[str]`. It returns the scare tools that **appeared since the
    last observation** while at least one remote tool is running.
    - Tools already running before the remote tool started don't count; the first observation after a remote tool appears is the
      baseline.
    - When no remote tool is running, reset the baseline.
- **`wire.poll_once`:**
  - Feed the same snapshot into the watcher.
  - For each new scare tool, publish `Risk("remote_access", "high", <redacted evidence>, app=<remote tool name>, source="risk.fakescan")`.
    Dedupe it through the existing `Deduper` with a key that includes the scare tool, so Event Viewer and then netstat can each warn once.
  - The Offer flow (confirm step, cooldown, close fallback, Call/Tell contact, auto-alert) must work unchanged. The action is
    `close_tool` for the remote tool.
- **Wording** (`copy.toml`, a new `[fake_scan]` section; `warning_for` uses it for `source == "risk.fakescan"`):
  - title: "This is a common scam trick."
  - body: "Someone connected to your computer just opened {scare}. Scammers show normal Windows messages to make it look broken. Your
    computer is probably fine."
  - spoken: one calm sentence, not a question.
  - actions: close_tool and explain.
  - Keep the existing copy tests' rules (title length, no digits and so on); read `tests/test_risk_copy.py`.
- **Polling:** keep the current poll interval. If a short-lived tool such as tree.com could start and end between two 10 s polls, note it
  as a limitation in the docs. Don't speed up polling.

## Tests (new `test_risk_fakescan.py`, about 12)
- No remote tool: nothing.
- A remote tool, then Event Viewer appears: one high warning with the `fake_scan` copy and the tool name.
- A scare tool already running before the remote session doesn't warn.
- The baseline resets after the remote tool closes.
- netstat after Event Viewer warns separately; the same tool again doesn't.
- cmd, powershell and tasklist never warn.
- `warning_for` uses the `fake_scan` wording.
- The Offer is `close_tool` and gets the contact button when a contact is configured.
- Case-insensitive names.
- Run:
  - `cd tests && python -m unittest test_risk_fakescan test_risk_wire test_risk_r2 test_risk_copy test_risk_actions test_risk_cooldown test_risk_contact test_proactive`
  - then full discovery. Sandbox-only failures in Windows speech and senses-lab socket are known; report the rest.

## Report
Files changed, test output, and a 3-step live demo recipe: which remote tool to open, then which scare tool, and what should appear.
