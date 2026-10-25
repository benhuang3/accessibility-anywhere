# Agent instructions

Shared rules for every coding agent on this repo (Claude Code, Codex, Cursor). Read the README for
what Ally does; this file is about how to work on it.

## Project

- Pure Python 3.11+ core in `ally/`, no required packages. Optional extras in `requirements-optional.txt`.
- `plugins/`: one Python function per tool. `dashboard/`: static HTML/JS/CSS served by the agent.
  `extension/`: the Chrome extension (Manifest V3). `config.toml` / `ally/default_config.toml`: settings.
- On Windows use `python`, not `python3`.

## Checks

Run the unit tests before saying a change is done:

```
cd tests
python -m unittest test_config test_registry test_brain test_network test_runtime test_handsfree
```

They use a fake Claude API and need no key. Do not run `e2e_chrome.py` or start Ally (`run.py`)
unless told to: both use port 8765, and other agents may be working at the same time.

## Working in parallel

Several agents work on this repo at once, each in its own git worktree and branch (`agent/<name>`).

- Edit only the files or folders your task names. If you need to change something outside them,
  stop and say so in your final message instead of editing it.
- Commit your work on your own branch with a clear message. Never push, merge, rebase or switch branches.
- Don't touch `.env`, `state/` or `logs/`.
- End with a short report: what you changed (files), test results, and anything left undone.

## Style

Match the surrounding code: standard library first, small functions, type hints where the file
already uses them, comments only where the reason isn't obvious. User-facing text is plain and short.
