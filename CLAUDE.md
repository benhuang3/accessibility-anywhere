@AGENTS.md

## Orchestrating Codex and Cursor

Claude Code is the lead. To hand a task to another agent, use `scripts/dispatch.ps1` (see its header).
Each agent works in its own worktree next to this folder (`../ally-codex`, `../ally-cursor`)
on branch `agent/codex` or `agent/cursor`. Review its diff and run the tests before merging into `main`.
