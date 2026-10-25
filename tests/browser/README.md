# Browser tests for the page steps

Real Chromium (Playwright) with the repo's `extension/` loaded, driving the service worker's `ACTIONS`
on local fixture pages. They check the contract in `docs/page_steps.md`. No Ally agent, no API key,
and never port 8765: fixtures are served on a free port picked by the OS.

## Run on a Mac

```
.venv/bin/python -m pip install playwright      # once
.venv/bin/python -m playwright install chromium # once
cd tests
../.venv/bin/python -m unittest browser.test_page_steps -v
```

`ALLY_HEADED=1 ../.venv/bin/python -m unittest browser.test_page_steps` shows the browser window.

## Run on Windows

First copy the repo to `C:\ally` with `scripts\win_setup.ps1` (it makes `C:\ally\.venv`), then:

```
powershell -ExecutionPolicy Bypass -File scripts\browser_tests.ps1
powershell -ExecutionPolicy Bypass -File scripts\browser_tests.ps1 -Headed
```

It installs Playwright and Chromium into `C:\ally\.venv` if they are missing, runs the tests and prints
the results.

Screenshots of the main states go to `logs/browser/*.png`.

## What's covered

- **Foundation:** the extension loads with no service-worker errors and every existing `steps/*.js`
  loads; `pageInfo`, `pageBaseline` (documented metric keys), `pageVerify` on an untouched page,
  `pageTeardown` twice; "style none" (`clearStyles`) keeps `ally_*` layers; `resetPage` removes
  `ally_*` layers, `data-ally-*` marks, Ally hosts and zoom, but keeps `#ally-risk-banner`.
- **Each step alone:** clutter hides the cookie banner (and the chat iframe) and off brings it back;
  menus shows a menu host with links and off removes it; `auditAtZooms` measures several levels and
  puts the zoom back; text with a zoom sets it and adds `ally_fit`; buttons adds `ally_buttons`
  without changing zoom; classify sorts the fixtures into general, article, form and excluded.
- **Combinations of clutter, menus, text, buttons:** every subset in canonical and reverse order
  gives the same end state (layers, marks, hosts, zoom); running twice equals once; turning one step
  off leaves the others' pieces alone; verify is ok after all of them; reset leaves nothing behind and
  the risk banner survives.

A step that isn't registered in `STEPS` yet is skipped with the reason, so the suite can run while
steps are still being written.

## Files

- `harness.py`: `FixtureServer` and `AllyBrowser` (`open`, `action`, `steps`, `styles`, `marks`,
  `page_state`, `zoom`, `reset_zoom`, `screenshot`, `worker_errors`, `page_console_errors`).
- `fixtures/combo_general.html`: cookie banner, sticky header with nav, hover mega menu and "Sign up",
  Intercom-titled chat iframe, 30-option select, 14px text, 24px buttons, long content.
- `fixtures/combo_article.html`, `combo_form.html`, `combo_login.html`, `combo_risk.html` (has a fake
  `#ally-risk-banner`).
