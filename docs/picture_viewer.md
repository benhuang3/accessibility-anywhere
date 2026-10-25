# Picture viewer ("zoom in on the picture")

The user says "zoom in on the picture of the bridge" and Ally shows that picture large in a viewer over the
page, where it can be zoomed and panned. Windows + Chrome only. No Claude yet: pictures are found by their
description (alt text, caption, label). Pictures without one are left alone for now.

## Behaviour

**Phrases** (fast path in `ally/rules.py`, checked before every other rule):
"zoom in on the picture", "zoom into the photo of the bridge", "zoom in on the bridge picture",
"magnify the image", "enlarge the photo", "show me the picture bigger", "make the photo bigger",
"open the picture", "show me the photo of the bridge". A picture word is required: picture(s), photo(s),
photograph(s), image(s), pic(s), painting(s), drawing(s), illustration(s). Without one, "zoom in" keeps its
meaning (page zoom / screen magnifier). Other wordings go to Claude when online, which can call
`zoom_picture` (its description says when).

Also: "close the picture" / "close the photo" -> `close_picture`; "next picture" / "another picture" /
"other picture" -> `next_picture`; "previous picture" / "picture before" -> `next_picture(back=True)`.
All of these are checked in `rules.match()` before `LEVEL` and the rules list (a `Rule` can't carry "of X").

**Which picture.** Only pictures on screen now (at least 40% visible, or a visible part of at least
200x200 CSS px) that have a description count. Decorative ones are skipped: `alt=""`, `role=presentation`
or `none`, `aria-hidden=true`, smaller than 80x80 CSS px.
1. "...of X" given: the picture whose description matches X best (score >= 0.5, section "Matching").
   Ties go to the bigger visible area. No match: "I don't see a picture of X on the screen." Nothing changes.
2. No X: if the eye tracker is confident (`source == "eyes"`) and its point (widened by its radius) is
   inside exactly one picture, that one. Otherwise the biggest picture nearest the middle
   (visible area x (1 - 0.5 x distance from centre / half-diagonal)). Never asks "which one?":
   Next / Previous in the viewer (buttons or voice) move between the described pictures on screen.
3. Pictures on screen but none described: "I can't tell what the pictures on the screen show yet, so I
   can't zoom in on one." No pictures at all: "I don't see a picture on the screen."
4. A named picture that's on the page but off screen is treated as not there (no scrolling for now).

**What the user sees.** A dark backdrop over the whole tab, the picture fitted to about 90% of it, at the
best resolution the page offers. Big buttons along the top: Zoom in (+), Zoom out (-), Previous, Next
(only when there's more than one), Close (Esc). The description in large text along the bottom. Drag or
arrow keys to pan (when zoomed in), mouse wheel or +/- to zoom (around the pointer), double-click to zoom
in at that spot. Zoom steps: 1, 1.5, 2, 3, 4, 6, 8 (1 = fitted).

**Spoken reply.** "Here's the picture: Golden Gate Bridge at sunset. Say zoom in to see more, or press Escape to
close it." (Not "go back" or "previous": those are browser navigation rules.) The description is cut to its first sentence and at most 120 characters.

**Getting out.** Esc, the Close button, a click on the backdrop, "close the picture", "reset" (removes it
with everything else), or the voice command "zoom out" when the picture is already at fitted size (only
voice: the viewer's own "-" button and the mouse wheel stop at fitted size, so they never close it by accident). Scroll position and focus
come back. While the viewer is open, "zoom in" / "zoom out" (voice, the popup's zoom buttons and the keyboard
shortcuts) zoom the picture, not the page: `ACTIONS.zoomStep` checks the viewer first and then returns the
unchanged page zoom. If the page zoom changes anyway (`zoom_to`, a site's own resize), the viewer refits.

This is not page zoom, so the rule "only 'zoom' zooms the page" still holds; the page and its zoom level
are never changed.

## Pieces (page-steps contract, docs/page_steps.md)

| Piece | Where |
|---|---|
| Page module `AllyPage.modules.picture` | `extension/page/picture.js` |
| Step `STEPS.picture` (on = open, off = close), `PAGE_FILES += page/picture.js` | `extension/steps/picture.js` |
| Overlay host `ally-picture-host` (in `HOSTS`, so reset removes it) | `extension/page/runtime.js` |
| Actions `pictureList`, `pictureZoom`, `pictureStep`, `pictureState` | `extension/background.js` |
| Matching and choosing (pure functions) | `ally/pagekit/picture.py` |
| Tools `zoom_picture`, `close_picture`, `next_picture`; viewer-aware `zoom_in` / `zoom_out` | `ally/tools/page_tools.py`, `ally/tools/system_tools.py` |
| Phrases | `ally/rules.py` |

Runtime changes: `VERSION` 2 (so open tabs get the new `HOSTS`), and `teardown()` runs the steps' off
functions in reverse order (the viewer restores the scroll lock it found, then the reader restores the page's).

No marks (`data-ally-*`) and no CSS layers: the page DOM is only read. Picture ids live in a `Map` of
id -> `WeakRef(element)` inside the module, rebuilt by every `list()`. Never touches `#ally-risk-banner`
(the viewer's z-index is 2147483646, below the banner's).

### Page module API (`extension/page/picture.js`)

- `list({minPx = 80})` ->
  `{view: {w, h}, win: {screenX, screenY, outerW, outerH, innerW, innerH, dpr}, items: [...]}` (the
  service worker adds `win.zoom` from `chrome.tabs.getZoom`), at most
  60 items, in reading order (top to bottom, then left to right). Each item:
  `{id: "p1", kind: "img" | "svg" | "bg", x, y, w, h, area, frac, descriptions: [{text, kind}], described, natural: {w, h}}`
  where `x, y, w, h` is the visible part in viewport CSS px, `area = w * h`, `frac` the visible share.
  Undescribed pictures are listed with `described: false` (for Claude later); decorative ones aren't listed.
- `open({id, order = []})` -> `{said: "opened the picture", id, description, src}`. `order` is turned into
  WeakRefs at open time, so a later `list()` can't make Next/Previous point at the wrong element. Throws
  "That picture isn't on the page any more." for an unknown or disconnected id.
- `zoom({direction: "in" | "out", closeAtFit = false})` -> `{open, scale, closed}`; "out" at scale 1
  closes the viewer only with `closeAtFit` (the voice tool passes it).
- `step({back = false})` -> `{open, id, description}`: the next (or previous) id in `order`, wrapping.
- `state()` -> `{open, id, scale}`.
- `close()` -> `{said: "closed the picture"}` or `{said: ""}` if none was open. `P.registerOff("picture", close)`.

**What counts as a picture:** `<img>` (including inside `<picture>`), inline `<svg>` with `<title>` or
`aria-label`, and elements with `role="img"` + `aria-label` whose computed `background-image` is a
`url(...)`. Not canvas, video, or anything inside iframes (only the top frame is searched). Pictures inside
Ally's reader view (`#ally-reader-host` shadow root) count (an explicit exception to `P.isAlly`, and their
cover test uses the shadow root's `elementFromPoint`); other Ally overlays don't.

**Visible:** `P.visible(el)`, intersected with the viewport. Covered parts don't count: of 5 points
(centre and 4 inner corners at 20%), the ones where `document.elementFromPoint` (or the reader's shadow
root's) isn't the picture or inside it are dropped; under 2 of 5 uncovered -> not visible.

**Descriptions, in order:** `alt` (non-empty) -> text of `aria-labelledby` -> `aria-label` -> the
`figcaption` of the nearest `figure` -> text of `aria-describedby` -> `title` -> the `aria-label` or
`title` of a surrounding link. SVG: `<title>` then `aria-label`. Each as `{text, kind}`
(kind: `alt`, `labelledby`, `label`, `caption`, `describedby`, `title`, `link`), trimmed, whitespace
collapsed, at most 300 chars, duplicates dropped.

**Best source for the viewer:** start with `img.currentSrc || img.src` (already loaded, so the page allows
it). If the element has a `srcset` (or a `<picture>` `<source>` whose `media` matches) with a bigger
candidate (largest `w`, else largest `x`), load that in the background and swap it in when it loads; on
error keep the first. Lazy placeholders: if `currentSrc` is a `data:` URL or the loaded image is under
50 px wide, use `data-src` / `data-srcset` / `data-lazy-src` / `data-original` instead. SVG: a deep clone,
sized to fit. Background pictures: the URL from the computed `background-image`.

**Stacking:** the host is appended last to `<html>` with z-index 2147483646. If Ganesh's
`#ally-risk-banner` is showing at the top, the button row starts below it.

**Viewer:** shadow-DOM host fixed over the whole tab (like `reader.js`: `lockScroll`, Esc in a capturing
keydown listener, focus restored on close). Image transform `translate(...) scale(...)`; pointer drag pans,
`wheel` zooms around the pointer, `dblclick` zooms in at the point. Panning is clamped so the picture
can't be dragged fully out of view. Buttons are at least 56 px tall with 22 px text. Caption at least
24 px, high contrast, at most 3 lines (ellipsis).

### Python

`ally/pagekit/picture.py`:
- `tokens(text) -> list[str]`: lowercase, letters and digits only, stop words dropped (articles, "of",
  "with", "showing", "picture", "photo", "image" and the other picture words), light stemming (trailing
  "ies" -> "y", "es", "s", "ing"), a small synonym table mapped to one word (car/automobile,
  dog/puppy, sea/ocean, kid/child, man/men, woman/women, ...).
- `score(about, descriptions) -> float`: shared tokens / tokens in `about`, +0.1 if the best matching
  description is `alt`.
- `choose(items, about="", gaze=None, threshold=0.5) -> {"item": dict | None, "why": str}` with why
  `"match"`, `"gaze"`, `"centre"`, `"no_match"`, `"none_described"`, `"none"`.
- `reading_order(items) -> list[str]`: ids of described items (already in reading order from the page).
- `description(item) -> str`: the first description's text; `spoken(text) -> str`: first sentence, <= 120 chars.
- `screen_to_viewport(x, y, win) -> (x, y)`: physical screen px (gaze) to viewport CSS px. Approximate
  (assumes Chrome's toolbar is all at the top), used only to pick between pictures.

Later, Claude: undescribed items are already listed. A future `describe_missing(ctx, items)` fills
`descriptions` with `{text, kind: "claude"}` from a screenshot crop (reuse `pageScreenshot` from
`ally/pagekit/review.py`) before `choose`, behind a config switch. Nothing else changes.

## Tests

- `tests/test_picture.py`: phrase routing (including "zoom in", "make the text bigger", "magnify where
  I'm looking" keeping their tools), tokens / score / choose / spoken / screen_to_viewport, and the tools
  against a fake browser.
- `tests/browser/test_picture.py` with fixtures `picture_basic.html`, `picture_edge.html`: list returns
  only visible described pictures (+ undescribed with `described: false`, no decorative / tiny / iframe /
  off-screen ones), open creates the host and loads the biggest srcset candidate, zoom / step / Esc /
  stepOff / pageTeardown, risk banner kept, page zoom and scroll position unchanged, no `data-ally-*`.
- In the VM: Wikipedia "Golden Gate Bridge", a BBC News article, a Met Museum object page.
