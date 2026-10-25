# Ben's tasks — handoff brief

Self-contained context for another agent. Assumes no prior knowledge of this session.

## Project

Ally, an always-on accessibility agent (Python 3.11+, Chrome extension, local dashboard) at the
repo root. CalHacks 13; Devpost submission due **Sun Oct 25, 9:00 AM PDT**. Product: proactive
accessibility for older adults — notice struggle, offer help, enlarge/read text, learn
accommodations.

## Who Ben is

Owns **Brain & OS** and is the **integration owner** who merges all branches. Repo:
`benhuang3/accessibility-anywhere`. Dev machine is macOS 13.7; a `.venv` on Python 3.13 holds the
optional packages; a Windows demo laptop/VM runs the Windows-only code. Notion HQ workspace:
"CALHACKS 26 WINNING TEAM NOTION SPACE" (reachable via the `notion-calhacks` MCP server).

## Feature in progress: edit a running program's text (focus: enlarging)

Four-tier ladder behind one tool `enlarge_text` in `ally/tools/ui_text_tools.py`:

- **Tier A, Chrome:** real in-memory edit via the extension (`setTabTitle`, `rewriteElement`,
  `restoreText`). Built, tested on Mac.
- **Tier A native + Tier C:** `ally/platforms/win_uia.py` (UI Automation read/write) wired into
  `windows.py` as `read_focused_box`; focused-element magnify via the existing `magnify_at`.
  Written, needs Windows VM validation.
- **Tier B, reading-card overlay:** `show_text_overlay` is a stub, blocked on Ganesh's
  `ally/overlay/` (foundation patch not yet applied).
- **Tier D, GDI injection:** design doc only at `docs/tier_d_gdi_text_hook.md`, not built, lowest
  priority.

Status: 2 builder + 2 verifier agents completed; both verifier SHOULD-FIX items resolved; 79 tests
pass; nothing committed. Working-tree changes span `ui_text_tools.py`, `win_uia.py`, `windows.py`,
`base.py`, `system_tools.py`, `rules.py`, `tools/__init__.py`, `extension/background.js`,
`requirements-optional.txt`, plus two new test files.

## Open tasks (from the Notion Tasks board; P0 unless noted)

1. Pick the demo laptop OS (Ganesh said Windows) and run `python3 run.py doctor` on it. Overdue.
2. Brain: simulate-event command + struggle/risk → offer → action routing. Overdue.
3. Reconcile the foundation patch (held by Ganesh, not in repo) with Hayden's `hayden/senses`
   branch into one `contracts.py` and one `senses/`. Due Fri 12:30 PM.
4. Get an ugly end-to-end version working. Due Fri 2 PM.
5. M0: squint/lean → offer → zoom end to end (shared with Hayden).
6. Create the GitHub repo + README (public by submission).
7. Run the fallback audit and add hardcoded/cached fallbacks. Due Sat 8 PM.
8. M3: auto-learn accepted offers (extend `remember_site_style`/`remember_preference`). Due Sun
   midnight, P1.
9. Feature freeze (owner). Due Sun midnight.

## Blocking dependencies

The foundation patch isn't applied, so Tier B overlay and the accommodation contract that
`enlarge_text` should match are both unconfirmed. Native/Windows code can be written on the Mac but
only validated on the VM.
