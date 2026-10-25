# Click by voice / by number, and Claude picture descriptions

Windows + Chrome only. Follows the page-steps contract (docs/page_steps.md): own host, no `data-ally-*`
marks on the page, reset removes everything, never touches `#ally-risk-banner`, per-tab queue.

## 1. Click by voice or by number

**Phrases** (`ally/rules.py`, checked before the other rules, like the picture phrases):
- "show numbers", "number the buttons", "show the links", "show me what I can click" -> `show_numbers()`
- "hide numbers", "hide the numbers", "numbers off" -> `show_numbers(on=False)`
- "click 7", "click number 7", "press 7", "tap 7", "number 7", "click seven" -> `click_number(7)`
  (number words one to ninety-nine), and a bare "7" / "seven" when numbers are showing (the tool checks;
  without numbers it says "Say show numbers first.")
- "click sign in", "click on the sign in button", "press continue", "tap search", "open the about link",
  "select the menu" -> `click_text(label)`. Words "button", "link", "tab", "box", "field" at the end are dropped.
- Browser navigation keeps its own words: "go back", "back", "previous" -> back; "go forward" -> forward;
  new: "reload", "reload the page", "refresh", "refresh the page" -> `browser_navigate(reload)`.
  "Back" and "forward" run `history.back()` / `history.forward()` in the page (Chrome's own Back skips pages the
  user never clicked or typed on, and a voice-only user never does: found in testing, "go back" jumped two
  pages). Pages extensions can't script fall back to `chrome.tabs.goBack` / `goForward`.
  "click back" / "click reload" click a page element with that label if one is on screen; otherwise they
  fall back to the browser's back / reload and say so ("There's no Back button here, so I went back.").

**Numbers.** Small, high-contrast badges (yellow on black, at least 18 px text, 28 px tall) at the top-left
corner of every clickable thing on screen: links, buttons, form fields, `[role=button|link|menuitem|tab|
checkbox|radio|switch|option]`, `summary`, `[onclick]`, `[tabindex>=0]` with a click handler is not
detectable, so not those. Only things at least partly in the viewport, visible (`P.visible`), not covered at
their centre (same `elementsFromPoint` walk as the picture viewer's cover test), and not inside Ally's own
overlays except the reader view and the picture viewer. At most 99, in reading order (top to bottom, left to
right). Badges follow scrolling and resizing (positions refreshed on `scroll`/`resize`, throttled to one
animation frame); numbers stay the same until "show numbers" is said again. Badges are hidden (not
removed from the mapping) for elements scrolled out of view.

Numbers go away when: "hide numbers", a click (after it, the page usually changes), reset, navigation
(the document goes away), and a back/forward-cache restore (`pageshow` with `persisted`: the old badges
would point at a page state the user may not remember, so they're removed; say "show numbers" again).

**Clicking.** `el.scrollIntoView({block: "nearest"})` if partly off screen, `el.focus({preventScroll:true})`,
then: form fields (text inputs, textareas, selects) are focused only ("I put the cursor in Email. Start
typing."); checkboxes/radios/buttons get `el.click()`. Links to another page are opened by the service worker
(`chrome.tabs.update`, or `chrome.tabs.create` for `target=_blank`): a script click isn't a user gesture, so
Chrome would block the new tab and mark the page as one the Back button skips (found in testing: "go back"
after a voice click jumped past the page). Trade-off: a link's own click handler doesn't run (single-page
apps reload instead of routing). A short yellow flash outline (400 ms, in Ally's
host) shows what was clicked. Links with `target=_blank` open as Chrome does. Returns what was clicked:
`{clicked: true, label, role, kind: "focus" | "click", navigates: bool (a link with an http(s) href)}`.

**Labels** (what `click_text` matches and what is spoken): `aria-label`, `aria-labelledby` text, the
element's visible text (`innerText`, whitespace collapsed), `value` of input buttons, `alt` of an image
inside, `title`, `placeholder`, the `<label>` of a form field. At most 80 characters.

**Matching a spoken label** (Python, `ally/pagekit/clicks.py`): normalise (lowercase, letters/digits,
drop "the", "a", "button", "link", "tab", "on"), then exact label match > label starts with the words >
all words in the label > most shared words (>= 0.5 of the spoken words). Ties: the visible one closest to
the centre of the screen. If two or more targets tie on the best score, numbers are shown on just those
and Ally asks "I see 3 called Sign in. Which number?" (`show_numbers(only=[...])`).

**Careful clicks.** Before clicking something whose label or surrounding form looks like paying, buying,
sending, deleting or signing out (`buy|pay|purchase|checkout|place order|order now|send|transfer|donate|
delete|remove|unsubscribe|sign out|log out|confirm`), Ally asks "That looks like it pays for something.
Click Buy now?" (voice yes/no via `ctx.agent.confirm.ask`, on-screen Yes/No works too). No answer or "no"
-> nothing is clicked. Without the voice-question feature, Ally asks the user to click it themselves.

### Extension API

Page module `AllyPage.modules.clicks` (`extension/page/clicks.js`), host `ally-click-host` (in `HOSTS`):
- `show({only = null, max = 99})` -> `{gen, items: [{n, label, role, x, y, w, h, href}]}` (`only`: a list
  of target ids from `list()` to number, for "which one?").
- `list({max = 300})` -> `{gen, view: {w, h}, items: [{id, label, role, x, y, w, h, href, inView}]}`:
  every clickable on screen with its label, without showing anything (for `click_text`).
- `click({n} | {id, gen})` -> the click result above, or throws "There's no number 12 on the screen." /
  "That button isn't on the page any more."
- `hide()` -> `{said: "hid the numbers"}` or `{said: ""}`. `state()` -> `{shown: bool, count}`.
- `P.registerOff("clicks", hide)`.

Step `STEPS.clicks` (`extension/steps/clicks.js`): `on` = show, `off` = hide. Actions in `background.js`:
`clickList`, `clickShow` (same as stepOn clicks), `clickDo`, `clickState` (a probe that never injects,
like `pictureState`). All take `tabId`, use `resolveTab` and run inside `withTab`.

### Python

`ally/pagekit/clicks.py`: `label_tokens`, `match(spoken, items) -> {"items": [best...], "score"}`,
`careful(label) -> str | None` (the kind: "pays", "sends", "deletes", "signs you out"),
`NUMBER_WORDS`, `parse_number(text) -> int | None`.
Tools (`ally/tools/click_tools.py`): `show_numbers(on=True)`, `click_number(n)`, `click_text(label)`.

## 2. Picture descriptions from Claude

When a picture has no description, Claude can provide one. Config `[picture] describe_with_claude = true`
(default true; off = the behaviour of docs/picture_viewer.md). Needs Ally online (`ctx.ask`).

**When:** `zoom_picture` when no described picture matches (`no_match` or `none_described`) and there are
undescribed pictures on screen; and the new `describe_picture(about="")` ("describe the picture", "what's in
this picture", "what does this photo show", "tell me about the picture"):
- viewer open -> describe the picture it shows (Claude even if it has alt text: the user asked what's in it;
  the alt text is given to Claude as context);
- otherwise -> choose a picture like `zoom_picture` (Claude descriptions included) and say its description
  without opening the viewer. "Say zoom in on the picture to see it bigger."

**How (one Claude call per request, ~2-4 s):** the page draws numbered outlines around the undescribed
pictures on screen (`picture.outline({ids})`, in Ally's host, removed right after), the service worker
takes one screenshot (`pageScreenshot`, existing), and Claude gets the screenshot with: "Each numbered box
is a picture. For each number, describe the picture in one short sentence (under 15 words) for someone who
can't see it well. Answer JSON only: {\"1\": \"...\", ...}." The answers become descriptions
`{text, kind: "claude"}` on those items, cached in the page module by picture source URL (so asking again on
the same page is instant), and then the normal matching runs. The spoken reply for a Claude description
says so once: "It looks like: a red tram on a hill."

The viewer for a picture described by Claude shows that description as its caption (`open({..., description})`).

Page module additions (`extension/page/picture.js`): `outline({ids})` / `unoutline()`, `remember({src:
text})` and descriptions from that cache in `list()` (kind "claude"), `open` accepts `description`.
Actions: `pictureOutline`, `pictureRemember`.

## Tests

- `tests/test_clicks.py`: phrases (including "go back", "reload", "click back", "click 7", "seven" alone
  only via the tool's check), label matching, careful words, number words; tools against a fake extension
  (ambiguous -> numbers on just those; careful -> asks; no answer -> no click; "click back" with no Back
  button -> browser back).
- `tests/test_picture.py` additions: Claude descriptions with a fake `ctx.ask` (JSON parsing, bad JSON,
  offline -> the old answer), cache.
- `tests/browser/test_clicks.py` with fixtures `clicks_a.html` (links to b, a "Back" link, buttons, a
  text field, checkbox, a link covered by a cookie bar, an off-screen button, `#ally-risk-banner`) and
  `clicks_b.html`: numbers shown/hidden, click by number navigates; **browser back restores page A from the
  back/forward cache with no stale numbers, and show numbers works again with fresh numbering; reload clears
  everything and works again; click by text; reset; numbers follow scrolling.** Describe via the picture
  outline + a fake Claude in the end-to-end tool test.
