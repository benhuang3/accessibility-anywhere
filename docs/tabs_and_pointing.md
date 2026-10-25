# Tab switching, "show me where to click", and highlighting an area

Windows + Chrome only. Page-steps contract as in docs/page_steps.md (own host, no `data-ally-*` marks, reset
removes it, never touches `#ally-risk-banner`).

## 1. Tabs (task 6, with back / forward / reload)

**Phrases** (`ally/rules.py`, checked before the other rules):
- "go to the other tab", "switch tabs", "the other tab", "go back to my last tab" -> `switch_tab("other")`:
  the tab used just before this one (Chrome's most-recently-used order, kept by the extension).
- "next tab" -> `switch_tab("next")`; "previous tab", "tab before" -> `switch_tab("previous")` (left and right
  in this window, wrapping); "first tab", "last tab" (the right-most).
- "go to tab 3", "tab three", "switch to the third tab" -> `switch_tab(number=3)` (in this window).
- "go to the gmail tab", "switch to my email tab", "show me the youtube tab" -> `switch_tab(name="gmail")`:
  matched against tab titles and site names (a few synonyms: email/mail/inbox, news, video/youtube).
- "what tabs are open", "list my tabs", "which tabs do I have" -> `list_tabs()`: "You have 3 tabs: 1, Inbox -
  Gmail; 2, Wikipedia; 3, usa.gov. You're on 2."

Replies: "Switched to Inbox - Gmail." / "There's only one tab." / "I don't see a tab called news."

**Extension:** `tabList` -> `{tabs: [{id, index, title, url, active, windowId, current}], recent: [tabIds]}`
(all normal windows; `current` = in the last focused window), `tabSwitch {tabId}` (activates it and focuses
its window). The most-recently-used order lives in `chrome.storage.session` ("recentTabs"), updated on
`tabs.onActivated` and `tabs.onRemoved`, so it survives the service worker sleeping.

## 2. "Show me where to click" and highlighting (task 7)

**Phrases:**
- "where do I click to sign in", "where do I click for search", "where is the search box", "where's the menu",
  "show me the sign in button", "point to the cart", "find the contact link" -> `show_where("sign in")`:
  a yellow box (and an arrow and a note "Click here: Sign in") around the matching control, scrolled into view
  if needed, plus a spoken position: "It's near the top right. I put a yellow box around it. Say click sign
  in to click it." Two or more equally good matches: boxes around all of them ("I see 2 called Sign in. I put
  boxes around both."). Nothing is clicked.
- "show me where to click" (no target) -> `show_numbers()` (numbers on everything clickable).
- "highlight the main text", "highlight the menu", "highlight the search box", "highlight the section about
  history" -> `highlight_area("main text")`: a spotlight: the rest of the page dimmed, the area outlined,
  scrolled into view.
- "stop highlighting", "clear the highlight", "remove the highlight" -> `clear_highlight()`.
  "highlight the links" keeps meaning the `highlight_links` page style.

**What can be found** (`AllyPage.modules.highlight.find`), on screen or off: controls (`AllyPage.CLICKABLE`,
labelled like click by voice), page areas (main / article = "main content text", nav = "menu navigation",
header = "top header", footer = "bottom footer", aside = "side panel", search = "search box", form = "form" +
its label) and sections (an h1-h4 heading, with its `section`/`article` if that isn't taller than three
screens). Matching is `ally/pagekit/clicks.match` on those labels; `show_where` prefers controls,
`highlight_area` prefers areas and sections.

**Page module** `extension/page/highlight.js`, host `ally-highlight-host` (in `HOSTS`, z-index 2147483644: below
the click numbers, the picture viewer and the risk banner; `pointer-events: none`, so the page stays usable):
- `find({max = 400})` -> `{gen, view: {w, h}, items: [{id, label, kind: "control" | "area" | "section", x, y,
  w, h, inView}]}`
- `show({ids, gen, mode: "point" | "spotlight", note})` -> `{shown, boxes: [{x, y, w, h}], view: {w, h}}`
  (positions after scrolling the first one into view; it follows scrolling and resizing).
- `clear()`; also cleared by Esc, a click anywhere on the page (point mode), reset, and a back/forward-cache
  restore. Animations respect `prefers-reduced-motion`.

Actions: `highlightFind`, `highlightShow`, `highlightClear`.

## Logging

New tools log to `ally.tools.tabs` / `ally.tools.clicks` at INFO (what was asked, what matched, what was
done), so a test run's flow can be followed in `logs/ally.log`; the extension logs each new action to the
service worker console with an `Ally:` prefix.
