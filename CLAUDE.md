@AGENTS.md

## Orchestrating Codex and Cursor

Claude Code is the lead. To hand a task to another agent, use `scripts/dispatch.ps1` (see its header).
Each agent works in its own worktree next to this folder (`../ally-codex`, `../ally-cursor`)
on branch `agent/codex` or `agent/cursor`. Review its diff and run the tests before merging into `main`.

## Working with several agents (subagents, background sessions, Codex, Cursor)

From Anthropic's "Building with Multi-Agent Teams" talk (Cal Hacks 13.0, Oct 24 2026), applied to Ally.

**How many.** Agents don't map one-to-one to features. Add one only if it gets something the lead can't:
a clean context, its own parallel lane, fresh eyes, or fewer tools. Otherwise do it yourself, and if a
script can do it, use a script. The number is the smallest of: pieces that could start now, pieces with
their own files, pieces with a real test, what you can follow, and what the budget covers. Usually 2-4,
never more than 5 for one feature. Build in stages: get one piece running for real before fanning out.

**Order.** Plan first (which parts are independent, which share files or data shapes, what command proves
each one). Then a read-only reviewer with a fresh context checks the plan or diff: give it a concrete job
("report every missing field / failing case"), not "any thoughts?", and stop after 3 rounds. Then builders.

**Who builds what.** One owner per file. Typical Ally split: extension (`extension/`), Python
(`ally/`), tests (`tests/`). Pieces that share files, or are judged by eye (layout, overlay look), stay
with the lead. Each agent works in its own worktree; worktrees split files, not the computer, so only
one thing may use port 8765, and only the lead drives the Windows VM.

**Handoff card.** Subagents never see this chat, only the brief and CLAUDE.md/AGENTS.md. Every brief has:
- GOAL: what done looks like, in one sentence.
- MUST KNOW: rules from the chat that matter here (e.g. "never zoom unless the user says zoom",
  "Windows only", "don't touch Ganesh's areas", "no inline scripts or styles in the dashboard").
- PROOF: the exact command, e.g. `python tests/timed.py 75 test_pagekit browser.test_page_steps`.
- DON'T TOUCH: files it must leave alone (plus `.env`, `state/`, `logs/`).
- STOP AFTER: a cap, e.g. 5 tries or 20 minutes, then report what's left.
- REPORT: under 200 words: files changed, what works, what breaks.

**Done means checked by someone else.** Agents grade their own work too kindly. "Done" comes from, in
order: the tests, then a read-only critic, never the agent's own word alone. For extension changes,
"done" also means it was checked with the extension and tabs reloaded (an old extension keeps running).

**Speed.** Team time = plan + briefs + slowest helper + merge, so split into equal-sized pieces and start
the slowest first. A team isn't faster by default: give the deadline up front ("you have 20 minutes").
Teams cost 3-10x the tokens of one agent; use them for depth or real parallel work, not quick fixes.
