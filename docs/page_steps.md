# Page steps: how Ally simplifies a web page

Five steps change or check a page in Chrome. Each one works on its own, and any combination works
together. "Simplified mode" is the four changing steps run as one, chosen by page type.

| Step | Name | Tool | What it does |
|---|---|---|---|
| Hide clutter | `clutter` | `hide_clutter(kinds, on)` | hides cookie banners, promo pop-ups, chat bubbles, ads, share bars, animation |
| Simplify menus | `menus` | `simplify_menus(on)` | Ally menu panel/strip with the site's links, big dropdown chooser, hover-menu grace delay |
| Enlarge text | `text` | `enlarge_text(goal="bigger" or "smaller")` | the page's own font sizes, bigger (`ally_text` layer, one rule per original size), plus a safe "fit" layer; **never zooms** (only "zoom in" zooms) |
| Enlarge buttons | `buttons` | `enlarge_buttons(on)` | bigger buttons, links and form fields (CSS layer); never changes zoom |
| Verify | `verify` | `check_page_layout()` | read-only: did anything get worse than the page before Ally? |

Outside Simplified mode, the picture viewer (`picture`, tools `zoom_picture` / `close_picture` /
`next_picture`) shows one described picture from the page large, over the page, without changing the page
or its zoom. Its own spec: `docs/picture_viewer.md`.

## The contract (every step follows it)

1. **Own pieces only.** A step owns its CSS layer names (`ally_<step>` or `ally_<step>_<part>`), its
   attribute marks (`data-ally-<step>="<kind>"` and `data-ally-<step>-<part>`) and its overlay hosts (ids in
   `AllyPage.HOSTS`). It never
   touches another step's pieces or a style the user saved.
2. **On adds, off removes only its own pieces.** Running a step twice gives the same state as once
   (`enlarge_text("bigger")` stepping zoom up is the one documented exception).
3. **One queue per tab.** Every page-changing action runs inside `withTab(tabId, ...)`. Actions take a
   `tabId`; without one they use the active tab once and pass that id along.
4. **Measure honestly.** Detectors and checks measure at the user's zoom, skip Ally's own hosts
   (`AllyPage.isAlly`), skip invisible elements and elements Ally hid. They can pause Ally's CSS layers with
   `AllyPage.withPaused(layers, fn)`: every layer's selectors start with
   `html:not([data-ally-pause~="<layer>"])`, so pausing is one attribute on `<html>`.
5. **Baseline.** `AllyPage.takeBaseline()` measures the page once with all Ally layers paused and keeps
   `{href, zoom, metrics}`. It is dropped when the URL changes or on teardown. Verify compares against it
   and only counts what got worse.
6. **Verify only reads.** On failure the runner backs off the most recent step of the current run first
   (buttons off, then zoom down one level, at most twice) and never touches steps that were on before.
7. **Reset removes everything Ally added:** `pageTeardown` calls every step's page-side `off`, removes all
   `data-ally-*` marks and every host in `AllyPage.HOSTS`, clears the baseline and the pause attribute, and
   removes the `ally_*` CSS layers. It never touches `#ally-risk-banner` (Ganesh's).
8. **Order when run together:** clutter, menus, text, buttons, then verify.

## Extension side (service worker)

Files are loaded at the top of `extension/background.js` with `importScripts`:

- `extension/steps/core.js` (foundation): `STEPS` registry, `withTab(tabId, fn)`, `resolveTab(args)`,
  `ensureRuntime(tabId)`, `inPage(tabId, path, args)`, `layerOn(tabId, name, css)`, `layerOff(tabId, name)`,
  `setZoomSettled(tabId, zoom)`, `pageTeardown(tabId)`.
- `extension/steps/<step>.js` (one per step): registers
  `STEPS.<step> = { async on(tabId, opts), async off(tabId, opts) }`. Both return a plain JSON object;
  include `said: "<short phrase>"` describing what changed (e.g. `"hid a cookie banner"`), or `""`.
- Page-side code lives in `extension/page/*.js`, injected as files into the page's isolated world by
  `ensureRuntime`. `extension/page/runtime.js` defines `globalThis.AllyPage`; each step's page file
  registers `AllyPage.modules.<step> = { ... }`. Background code calls them with
  `inPage(tabId, "<step>.<fn>", args)`, which may return a Promise.

ACTIONS (called by the agent over the WebSocket; all accept an optional `tabId`):

| Action | Args | Returns |
|---|---|---|
| `pageInfo` | | `{tabId, url, zoom, defaultZoom, type, reasons, articleScore, simple}` |
| `pageBaseline` | `force` | the baseline summary |
| `auditAtZooms` | `levels, textGoalPx, targetGoalPx` | `{tabId, url, original, defaultZoom, audits:[{zoom, ...metrics}]}` |
| `setZoomSettled` | `zoom` | the zoom now set |
| `stepOn` / `stepOff` | `step, opts` | the step's result object |
| `pageVerify` | | `{ok, checks:{overflow, clipped, sticky, covered}, zoom}` |
| `pageTeardown` | | `true` |
| `pictureList` / `pictureStep` / `pictureZoom` | see `docs/picture_viewer.md` | |
| `pictureState` | | `{open, id, scale}`; a probe that never injects (safe on any tab) |

## AllyPage (page side, `extension/page/runtime.js`)

`HOSTS`, `isAlly(el)`, `visible(el)`, `key(el)`, `mark(el, step, kind)`, `marked(step, kind?)`,
`unmark(step, kind?)`, `withPaused(layers, fn)`, `measure(opts)`, `takeBaseline(meta)`, `getBaseline()`,
`verify(meta)`, `registerOff(step, fn)`, `teardown()`, `modules`, `run(path, args)`.

`measure()` returns CSS-pixel metrics (the caller multiplies text and target sizes by the zoom):
`{href, vw, vh, textPx, textSizes:[[px, shareAtOrBelow]], overflowX, overflowPx, cutOffText, clipped:[key], stickyCover,
covered:[key], smallTargets:[{key, w, h}], targets}`.

## Python side

- `ally/pagekit/core.py`: `STEP_ORDER`, `PageSession(ctx)` (binds the tab id, wraps `ctx.browser.call`),
  `run_steps(ctx, steps, on, opts)` (order, queue-friendly calls, verify, back-off, spoken summary),
  `steps_for(page_type, requested)` (Simplified mode policy).
- `ally/pagekit/zoom.py`: `choose_zoom(audit, goals)` (pure).
- `ally/tools/page_tools.py`: the tools above plus `simplified_mode(on)`.

## Simplified mode

Sort the page first (`pageInfo.type`):

| Type | Detected by | Steps |
|---|---|---|
| `excluded` | password/card/IBAN fields, checkout/pay/login URLs, open non-clutter dialog, risk banner showing, PDF / chrome:// / Web Store | none (says why) |
| `article` | readerable score | reader view (headings, links and lists kept) + Ally menu strip |
| `form` | > 5 form controls | clutter (cookie, chat, promo only), text, buttons |
| `general` | everything else | clutter, menus, text, buttons |

It speaks first, shows a "Simplifying..." card while measuring, shows a "Simple mode on - Undo" chip while
on, and remembers a site only after an explicit yes. On page load the extension re-applies remembered sites
in JS only (clutter, menu strip, fit and buttons layers at Chrome's remembered zoom); it never re-measures
zoom on load.

## AI check and fix (after the steps)

When Claude is available (`[page_enlarge] ai_check`), every page-changing request continues after the
deterministic steps and verify, **in the background** (`ai_background`): Ally replies at once, then:

1. One Claude call per round gets a screenshot from before the request and one from now (800 px wide), the
   verify result and a page outline (`pageOutline`), and answers `{worse: [...], actions: [...]}`. Only
   regressions count; "not as big as hoped", content further down, or a sidebar folded into a menu don't.
2. Actions are limited to: a Chrome zoom level, turning one of this run's steps off, or CSS rules from a short
   list of layout properties in the `ally_fix` layer (`STEPS.fix`, checked again in the page: never hides
   dialogs, navigation, main content or `#ally-risk-banner`).
3. Up to 3 fix rounds. A round that leaves more problems is undone. If it still looks wrong, everything the
   request did is reverted and Ally says so. It speaks only when it changed something.
4. A newer request on the same tab cancels a running check. Skipped offline, on excluded pages, and for
   articles (the reader view replaces the page).
