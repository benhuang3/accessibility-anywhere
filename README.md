# Ally: an always-on accessibility agent for older adults

Ally runs in the background on a Windows PC. You talk to it, type to it, or press a hotkey, and it changes the
screen and the web for you: bigger text and buttons, simpler pages, a screen magnifier that follows your eyes,
reading aloud, clicking by voice, and a calm safety check when something looks like a scam. It also notices when
you're struggling (squinting, leaning in, clicking the same spot) and offers to help.

Built for Cal Hacks 13.0 by Ben H (brain and OS layer, integration), Hayden (senses and eye tracking), Ganesh
(scam protection, overlay, Ally Call, demo) and Ben W (voice).

- **Hands-free**: "Ally, make it bigger". Ally listens for its name; speech is turned into text on your computer.
- **Simplifies any website**: "Ally, simple mode" hides pop-ups, ads and clutter, adds one big menu, and makes
  the text and buttons bigger, chosen for the kind of page. Every change can be undone.
- **Knows where you're looking**: the webcam eye tracker magnifies what you look at, scrolls when you look at the
  bottom of a page, and lets you answer a question by looking at Yes or No.
- **Notices struggle**: a squint plus a lean, or repeated clicks, become an offer: "Having trouble reading? Want
  me to make it bigger?"
- **Protects you**: fake virus pop-ups, lookalike bank sites, remote-control tools and trapping pages get one calm
  panel that asks before closing anything, plus a one-tap call to a trusted helper (Ally Call).
- **Always on**: auto-restart on crash or freeze, start at login, offline mode without internet, and a panic button
  that undoes everything.
- **Easy to change**: settings, commands, hotkeys and page styles live in `config.toml`, which reloads when you
  save. A new tool is one Python function in `plugins/`.

Windows is the main platform. Linux (GNOME) keeps the core; the Mac version was removed.

### How we use Cartesia

Voice is Ally's main interface, and every word it says is **Cartesia Sonic** (`sonic-3.6`, then `sonic-3`):

- **Speech shaped for older listeners**: offers are calm and a little slower, scam warnings calm (never alarmed) and
  slower still with a pause before the question, apologies sympathetic; emotion, speed, volume and language are set
  per sentence (`ally/speech.py`, `ally/voice_confirm.py`).
- **Never silent**: Ally's own prompts are pre-rendered by Sonic and cached, a circuit breaker switches to the
  computer's voice during an outage, and a model the account can't use is skipped.
- **Listening with Ink**: spoken yes/no answers are transcribed with `ink-whisper` (about 700 ms from the end of
  speech to the answer); a streaming Ink-2 turn-detection client is built and tested.
- **Reading pages aloud**: long pages a part at a time ("keep reading"), and in 21 languages: "read this page
  in Spanish" has Claude translate and Sonic speak it with `language: "es"`.

119 unit tests, a 21-scenario end-to-end run and a 184-answer phrase set against a fake Cartesia server.
Full write-up: **[docs/CARTESIA.md](docs/CARTESIA.md)**.

### How we use SpacetimeDB

SpacetimeDB is the whole backend of **Ally Call**, the one-tap call to a trusted helper during a scam. One TypeScript
module (`spacecall/server/src/index.ts`) replaces an app server, a media relay and a database:

- **Real-time media over reducers and event tables**: 16 kHz audio, camera JPEGs and the elder's live screen (sent by
  the Python agent through SpacetimeDB's HTTP API at up to 15 fps) flow as event-table rows to exactly one recipient.
- **The helper can act on the elder's screen**: highlight, tap, drag and close-tab gestures travel as
  `helper_action_event` rows and come back as `action_result_event` rows once the PC has carried them out.
- **Privacy enforced by the server**: per-incident capability rooms, roles checked against `presence` by identity,
  row-level visibility filters on every event table, private tables read only through identity-filtered views,
  strict validation in every reducer, and automatic expiry after two hours or on disconnect.
- **Face profile sync**: the eye tracker's profile is stored privately per identity in the same module (optional).

8 tables, 14 reducers, a disconnect hook, 2 views, 5 visibility filters, and 154 tests (79 TypeScript, 75 Python).
Full write-up: **[docs/SPACETIMEDB.md](docs/SPACETIMEDB.md)**.

---

## Quick start (about 5 minutes)

1. **Python 3.11 or newer, 64-bit.** On Windows type `python` (not `python3`) everywhere below.
2. **Get the code** and open a terminal in this folder.
3. **Add your API keys**: copy `.env.example` to `.env`, then paste your Anthropic key after `ANTHROPIC_API_KEY=`
   (and `CARTESIA_API_KEY=` for the natural voice). Without a key Ally runs in offline mode with simple commands.
4. **Install the extras** (recommended): `python -m pip install -r requirements-optional.txt`
   (`scripts/win_setup.ps1` installs the Windows ones).
5. **Start Ally**: `python run.py`. It says "Ally is ready"; the dashboard is at http://127.0.0.1:8765.
6. **Add the Chrome extension**: `chrome://extensions` > **Developer mode** > **Load unpacked** > the `extension`
   folder. After pulling new code, press the extension's reload arrow **and reload open tabs**.
7. **Set up your face once**: say "Ally, set up my face" (about 90 seconds; see Eye tracking).
8. **Check everything**: `python run.py doctor` lists what works and how to fix what doesn't.
9. **Start at login** (optional): `python run.py install` (undo with `python run.py uninstall`).

| System | Notes |
|---|---|
| **Windows** | The magnifier and color filters use the Windows Magnification API directly (Ally closes the Windows Magnifier app and takes over). Speech uses Cartesia, or the built-in Windows voices as a fallback. Hotkeys ignore key presses sent by other programs. "Read the selection" (Ctrl+Alt+R) copies once you let go of Ctrl+Alt and puts your clipboard back. |
| **Linux** | Magnifier, color filters and text size need GNOME. Install `espeak-ng` or `speech-dispatcher`, and `xclip` or `wl-clipboard`. On Wayland run `python run.py gnome-shortcuts` and set `hotkeys_enabled = false`. Eye-following, the name of the control under your gaze, and eye scrolling outside Chrome are Windows-only. |

---

## What you can say

Say it ("Ally, ..."), type it in the dashboard or the extension popup, press the voice hotkey, or run
`python run.py send "..."`. Common requests are answered instantly by built-in rules, and loosely worded ones are
matched to the nearest rule ("could you maybe make the words larger"); anything else goes to Claude, which picks
from the same tools. Clicks, reset and anything that sends data are never guessed this way.

| To... | Say |
|---|---|
| Make a page easier | "simple mode", "this site is too busy", "simple mode off" |
| Bigger or smaller | "make the text bigger", "smaller", "bigger buttons", "zoom in" (only "zoom" zooms) |
| Clear the clutter | "hide the pop-ups", "declutter", "simplify the menu", "check the page" |
| Read | "read this page", "keep reading", "read this page in Spanish", "read the selection", "reader mode", "stop" |
| Pictures | "zoom in on the picture of the bridge", "describe the picture", "next picture", "close the picture" |
| Click | "show numbers", "click 7", "click sign in", "press continue" |
| Find things | "where do I click to sign in", "where is the search box", "highlight the menu", "stop highlighting" |
| Browse | "go back", "go forward", "reload", "switch back to Facebook", "next tab", "go to tab 3", "list my tabs" |
| Use your eyes | "magnify what I'm looking at", "what am I looking at", "scroll with my eyes", "hands-free scrolling off" |
| The whole screen | "magnifier", "invert colors", "high contrast", "what's on my screen?" |
| Undo | "reset" (or Ctrl+Alt+0): every visual change goes back |

"Help" lists the phrases.

### Making web pages easier (page steps)

Each change is its own step, and they work alone or together. The extension does the page work;
`ally/pagekit/` decides the order (clutter, menus, text, buttons), checks the page still looks right, and undoes
the last change if it broke something.

- **Bigger text** grows the page's own font sizes (at least 30% bigger), keeping headings bigger than body text.
  It never zooms the page.
- **Bigger buttons** grows labels about 1.3x and click areas, including links styled as buttons.
- **Hide clutter** hides cookie banners, sign-up pop-ups, chat bubbles, ads, share bars and animation. It never
  clicks Accept or Reject and never hides the page's own important messages or Ganesh's risk banner.
- **Simple menu** puts the site's menu into one big "Ally menu" panel.
- **Simple mode** combines them, chosen by page type: articles open in a large, high-contrast reader view;
  other pages get all four steps; sign-in, payment and bank pages are left alone. A consent pop-up is hidden
  first instead of stopping it. A "Simple mode on · Undo" chip stays on the page.
- **AI check** (optional, on by default with a key): after the steps, Claude compares a before/after screenshot
  of the tab and fixes what got worse, up to 3 tries, then puts the page back. It runs in the background.
  `[page_enlarge] ai_check`.

Spec: `docs/page_steps.md`.

### Pictures

"Zoom in on the picture (of X)" opens the picture in a viewer over the page, at its full resolution, with big
Zoom in / Zoom out / Previous / Next / Close buttons; drag or use the arrow keys to pan. Ally reads the picture's
description aloud. Pictures are matched by their alt text, caption or label; when nothing matches, Claude looks
at the pictures on screen in one screenshot and describes them. "Describe the picture" says what one shows.
`[picture] describe_with_claude`. Spec: `docs/picture_viewer.md`.

### Clicking, finding and tabs

- **Click by number**: "show numbers" puts yellow numbered badges on everything clickable; "click 7" clicks it.
- **Click by words**: "click sign in". Two matches get numbers and "Which number?". Anything that looks like
  paying, sending, deleting or signing out asks yes/no first. Text boxes get the cursor instead of a click.
- **Show me where to click**: "where do I click to sign in" puts a yellow box and arrow on it without clicking.
  "Highlight the menu / the section about prices" dims the rest of the page.
- **Browser**: "go back" and "go forward" run in the page, so they never skip a page you only used by voice. "Switch
  back to Facebook", "the other tab", "next tab", "tab 3" and "list my tabs" switch tabs.

Specs: `docs/click_by_voice.md`, `docs/tabs_and_pointing.md`.

### Hands-free voice

Start a request with **"Ally"** (or end with it): "Ally, zoom in", "Hey Ally, read this page". Say just "Ally" and
it beeps; then say the request. After an answer you have a few seconds for a short follow-up without the name
("a bit more"). While Ally is talking, say **"Ally, stop"**. The wake word copes with how speech-to-text spells
it ("Allie", "Alley", "Ollie") and a filler word or two before it.

While you talk to Ally, the bear in the corner shows "Listening…", your words, then the reply as it's spoken, and
fades a few seconds after. Safety cards, offers and calls always take priority.

- Needs `pip install faster-whisper`. The speech model (base.en, about 145 MB) downloads once and runs on your
  computer. Windows shows the microphone as in use while Ally listens.
- `[voice]`: `always_listen`, `wake_words`, `follow_up_seconds`, `whisper_model`.

### Spoken offers and yes/no answers

Ally speaks first ("Want me to make this bigger?") and listens for the answer; the on-screen Yes / No buttons work
too, and the first answer wins. Silence or an unclear answer never counts as yes: an unclear answer gets one
re-ask, then "You can tap Yes or No". "What?" and "slower" replay the question more slowly. Code:
`ally/voice_confirm.py`.

- **Voice**: Cartesia Sonic (`sonic-3.6`, then `sonic-3`), a little slower for questions and slower still for
  warnings. Set `[speech] cartesia_voice_id` (a name like "Tessa" or an ID). `python run.py voices` lists voices;
  `python run.py voice-test Tessa Leo --play` compares them.
- **Fallbacks**: Cartesia -> fallback model -> the built-in voice. Fixed prompts are cached on disk, so they keep
  the Cartesia voice offline. Answers are heard with Cartesia Ink, then offline Whisper; with neither, the buttons.
- **Try it**: `python run.py say "..."`, `python run.py ask "Want me to make this bigger?"`, `python run.py answer
  yes`, `python run.py warn scam_popup`.
- `[confirm]` (listen time, re-ask, speeds) and `[speech]`.

### Eye tracking

1. **Set up once**: say **"Ally, set up my face"** (or "set up my eyes"). About 90 seconds: turn your head slowly
   in a circle, then 9 dots, sit back a little, lean in a little, read a short passage, smile, and squint at tiny
   text; five check dots finish it. Esc cancels. Redo it after moving your laptop or chair a lot. "Quick calibrate"
   re-tunes it in about 6 seconds.
2. **"What am I looking at"** names the area of the screen and, on Windows, the button, link or text there.
3. **"Magnify what I'm looking at"** (or Ctrl+Alt+G) zooms around where you looked and becomes a reading aid: the
   view glides along the line as you read and returns to the start of the next line. Looking near an edge pans
   that way. "Follow my eyes slower / faster" changes the pace (`[gaze] pan_speed`). Moving the mouse hands control
   back to it. "Reset" or "magnifier off" stops it.
4. **Eye scrolling** (not zoomed in): look near the bottom of a page for a moment to scroll down, near the top to
   scroll up; the closer to the edge, the faster. In Chrome the edges are the page's own, so looking at the tabs
   never scrolls. It pauses while you use the mouse. "Scroll with my eyes" / "hands-free scrolling off";
   `[gaze] scroll`, `scroll_speed`, `scroll_band`, `scroll_dwell`.
5. **"Show where I am looking"** draws a thin ring with a centre dot where the tracker thinks you look
   (click-through); "hide the eye dot" removes it.
6. **Answer by looking**: yes/no choices can show as large cards; look at one for a moment.

If the eyes aren't set up, your face isn't visible, or the estimate is too unsure (`max_radius_px`), Ally uses the
mouse pointer and says so. A webcam is far less precise than a dedicated eye tracker: areas of the screen are the
useful part, and light on your face helps. During a call, the eye tracker hands the webcam to the call and takes
it back after.

- Needs `pip install mediapipe opencv-python` and a webcam; the default `v5` features also need `pip install
  onnxruntime` and `python scripts/get_gaze_models.py`. Video is processed on your computer and never saved; only
  the calibration (a few numbers) is kept in `state/`. Clicks quietly improve the estimate (`gaze.click_learning`).
- "Turn off eye tracking" stops the camera until Ally restarts; `gaze.enabled = false` for good.

### Noticing struggle (senses)

- **Face** (webcam): squinting and leaning toward the screen, each against *your* resting face; blinks, smiles,
  winks, turning away and darkness are ignored. The face setup learns your resting face, lean and a personal squint
  model; Ctrl+Alt+C reruns a short face-sensing pass.
- **Mouse and keyboard**: clicking the same spot again and again, deleting and retyping, and resting a long time
  over a control without clicking. Only *that* a key was pressed is counted, never which key.

Two different signals within a few seconds become one **struggle** event (`agent.on_struggle`,
`agent.senses.subscribe`). The proactive layer (`ally/proactive.py`) turns it into one offer: the bear in the
corner asks, Ally says it out loud, and you answer by voice or button (20 s timeout). Saying no quiets that kind of
offer for a while (`[senses] decline_seconds`). "Stop watching me" turns sensing off until Ally restarts.
`python run.py tool senses_hud` shows every signal live; `python run.py tool simulate_struggle '{"kind": "demo"}'`
runs a demo without a webcam. Optional LibreFace action units: `python scripts/get_au_models.py` (USC research
license; `python run.py doctor` reports it).

### Scam protection and Ally Call

Ally looks for fake virus warnings, lookalike bank addresses, card forms on unfamiliar sites, remote-control tools
running while a bank page is open, fake "scans" during a remote session (Event Viewer, netstat), and pages that try
to trap you. Local rules check first; with screen review on, Claude gives a second opinion on a flagged screen.

The **bear** in the bottom-right corner shows one calm card with an amber "Safety check" badge: what it sees, what's
safe to do, and **Yes, close it** / **Keep it open**. "Keep it open" asks "Are you sure? Scammers often say it's safe
to continue." before snoozing that warning for 30 minutes.

**Call Priya** (your trusted helper) opens **Ally Call**, a small mini-player in the corner, built on SpacetimeDB
(`spacecall/`, see [docs/SPACETIMEDB.md](docs/SPACETIMEDB.md)): the helper sees your screen on their phone and can **Highlight** something, **Use your screen**
(tap to click, drag to scroll; never typing) or **Close a tab**. Google Meet remains the fallback. `[risk]`:
`trusted_contact`, `contact_topic`, `call_service` (`meet` or `spacecall`), `spacecall_url`, `screen_share`,
`auto_alert`, `screen_review`.

Screen review is **off by default** and budget-capped; contact alerts never include the page address or content.
Evidence and limits (small, self-written evals: rules 9/14, rules + AI 13/14 on rendered pages; 3 of 500 popular
sites warned, none high): `docs/RISK_HANDOFF.md`.

---

## Hotkeys and command line

| Keys (`<mod>` = Ctrl+Alt) | Action |
|---|---|
| `<mod>+V` | Talk to Ally (press again to stop listening) |
| `<mod>+R` | Read the selected text aloud (any app) |
| `<mod>+S` | Stop talking |
| `<mod>+=` / `<mod>+-` | Zoom in / out (the page if Chrome is in front, else the screen) |
| `<mod>+0` | **Panic button**: undo every visual change |
| `<mod>+M` | Screen magnifier on/off |
| `<mod>+G` | Magnify where you're looking |
| `<mod>+E` | Describe what's on the screen |
| `<mod>+C` | Recalibrate face sensing |
| `<mod>+A` | Open the dashboard |

The extension's own shortcuts work even when the agent is off: Alt+Shift+Period / Comma (zoom), Alt+Shift+0
(reset), Alt+Shift+R (read page).

```
python run.py              start (with auto-restart)       python run.py status    what Ally is doing
python run.py doctor       check setup                     python run.py stop      stop Ally
python run.py send "..."   send a request                  python run.py restart   restart Ally
python run.py tool NAME '{"arg": 1}'   run one tool        python run.py check     validate config.toml
python run.py agent        run without auto-restart (development)
```

---

## Make it do anything

1. **Settings: `config.toml`.** Created on first start, fully commented, editable in the dashboard. Changes apply
   within a second; a mistake keeps the last good settings and shows the exact problem. Highlights: `[brain]`
   (model, effort, system prompt), `[hotkeys]`, `[profile]` (defaults for every page), `[page_styles]` (CSS for
   `restyle_page`), `[page_enlarge]`, `[gaze]`, `[senses]`, `[risk]`, `[tools] disabled`.
2. **Commands (no code)**:
   ```toml
   [commands.morning_news]
   description = "Open the news in large, high-contrast text and read the top story."
   phrases = ["morning news", "read me the news"]
   hotkey = "<mod>+n"
   steps = [
     { open = "https://text.npr.org" },
     { wait = 2 },
     { tool = "restyle_page", args = { style = "high_contrast" } },
     { ask = "Read the first headline and its summary aloud." },
   ]
   ```
   Step types: `tool`, `say`, `ask` (a request for Claude), `open`, `keys`, `wait`, `notify`, `shell`. Each command
   is also a tool Claude can choose (`allow_ai = false` prevents that).
3. **Phrases**: a new rule in `ally/rules.py` also needs example phrasings in `rules.EXAMPLES` (a test checks
   this), so loose wording can find it.
4. **Tools (Python)**: drop a file in `plugins/`; it loads instantly and reloads on save, and a broken file never
   stops Ally.
   ```python
   from typing import Annotated
   from ally import tool, ToolError

   @tool
   def bigger_cursor(ctx, size: Annotated[int, "1 = normal, up to 4"] = 2):
       """Make the mouse pointer bigger on web pages. Use when the user can't find the pointer."""
       if not 1 <= size <= 4:
           raise ToolError("Size must be 1 to 4.")
       ctx.run("restyle_page", style="big_cursor", on=size > 1)
       return f"Pointer size {size}."
   ```
   The docstring is what Claude reads; type hints become the schema; `ctx` gives `ctx.browser.call(...)`,
   `ctx.os`, `ctx.say`, `ctx.ask(..., image=...)`, `ctx.run`, `ctx.state`, `ctx.config`. `@tool(ai=False)` hides a
   tool from Claude. Examples: `plugins/example_tools.py`, `plugins/plain_language.py`.
5. **Browser actions**: add a function to `ACTIONS` in `extension/background.js` (page work goes in a page step:
   `extension/steps/` and `extension/page/`, see `docs/page_steps.md`), reload the extension, and call it with
   `ctx.browser.call("yourAction", x=1)`.

---

## Built-in tools

| Area | Tools (`*` = not offered to Claude: hotkeys, rules and the dashboard only) |
|---|---|
| Web pages | `simplified_mode`, `enlarge_text`, `enlarge_buttons`, `hide_clutter`, `simplify_menus`, `make_page_easier`, `check_page_layout`, `open_reader`, `reader_view`, `restyle_page`, `set_page_zoom`, `set_min_font_size`, `remember_site_style`, `read_page`, `get_page_text`, `scroll_page`, `restore_ui_text` |
| Pictures | `zoom_picture`, `describe_picture`, `next_picture`, `close_picture` |
| Clicking and finding | `show_numbers`, `click_number`, `click_text`, `show_where`, `highlight_area`, `clear_highlight` |
| Browser | `browser_navigate`, `switch_tab`, `list_tabs`, `open_website` |
| Whole screen | `magnify_screen`, `toggle_magnifier*`, `color_filter`, `set_text_size`, `describe_screen`, `read_selection` |
| Eyes and senses | `face_setup`, `calibrate_eye_tracker`, `check_eye_tracker`, `where_am_i_looking`, `magnify_where_i_look`, `magnifier_follow`, `eye_follow_speed`, `eye_scroll*`, `show_gaze`, `choose_by_looking`, `eye_tracking`, `calibrate_face_sensing`, `struggle_sensing`, `senses_hud*`, `simulate_struggle*` |
| Voice | `ask_yes_no`, `answer_question*`, `warn_risk*`, `set_speech_rate`, `listen*`, `stop_speaking*`, `list_voices*` |
| System | `reset_all`, `apply_profile`, `remember_preference`, `forget_preferences`, `zoom_in*`, `zoom_out*`, `zoom_to*`, `open_dashboard*`, `show_help*` |

---

## How it stays on

| Problem | What Ally does |
|---|---|
| Agent crashes / freezes | The supervisor restarts it (1 s, 2 s, 4 s ... 30 s apart); `/health` checked every 5 s |
| Computer restarts | `run.py install` starts Ally at login |
| No internet / no key | Offline rules handle common requests; commands and hotkeys keep working |
| Chrome closed or agent restarted | The extension reconnects by itself |
| Bad config edit / broken plug-in | Last good settings / old plug-in version stay active; the error is shown |
| Hotkey hook or camera crashes | Each runs in its own process and is restarted |
| A tool hangs | Every tool has a timeout (15 s default) |
| A page change breaks the layout | Page steps check the layout and undo the change that broke it |
| Something looks wrong | Ctrl+Alt+0 or "reset" undoes every visual change |

---

## How it works

```
 you: voice / hotkey / typing / a look / a struggle signal
            |
   +--------v------------------------------ Ally agent (Python, run.py) ------------------------+
   |  listener, hotkeys, senses, eye tracker (helper processes)                                  |
   |        |                                                                                    |
   |  brain: 1) fast path: commands + phrase rules   2) Claude tool use   3) offline rules        |
   |  proactive coordinator: struggle -> one offer (bear overlay + voice) -> tool                 |
   |  risk wire: page reports + processes -> safety check -> close / Ally Call                    |
   |        |                                                                                    |
   |  tool registry: built-in tools + plugins/*.py + [commands]                                  |
   |     |                    |                         |                       |                |
   |  OS layer (Windows)   pagekit (page steps,      speech + voice          Chrome bridge        |
   |  magnifier, colors,   order, verify, AI check)  (Cartesia / OS voice)   (WebSocket)          |
   |  UI Automation, keys                                                        |                |
   +-----------------------------------------------------------------------------|----------------+
        supervisor (restarts it)                       Chrome extension: page steps (clutter, menus,
                                                       text, buttons, simple mode, reader, pictures,
                                                       click numbers, highlights), tabs, risk reports
```

| Path | What it is |
|---|---|
| `run.py`, `ally/cli.py` | Commands |
| `ally/agent.py` | Wires everything together |
| `ally/brain.py`, `ally/claude.py`, `ally/rules.py` | Decides what to do (phrase rules, Claude, offline) |
| `ally/registry.py`, `ally/tools/` | `@tool`, schemas, timeouts; the built-in tools |
| `ally/pagekit/` | Page steps: order, layout check, back-off, AI check (`review.py`), picture/click/tab matching |
| `ally/platforms/` | Windows (Magnification API, UI Automation, reading cards) and Linux adapters |
| `ally/gaze*.py`, `ally/face_profile.py` | Eye tracking, face setup, gaze ring, eye scrolling (`gaze_scroll.py`) |
| `ally/senses/` | Struggle signals (face, mouse, keyboard) |
| `ally/proactive.py`, `ally/voice_bus.py`, `ally/overlay/` | Offers, spoken offers, the bear overlay |
| `ally/risk/` | Scam protection, Ally Call wiring, remote touch |
| `ally/speech.py`, `ally/voice*.py`, `ally/listener.py` | Speech (Cartesia Sonic), yes/no answers (Ink), hands-free listening (`docs/CARTESIA.md`) |
| `ally/server.py`, `ally/bridge.py`, `dashboard/` | Dashboard, API, extension link |
| `extension/` | Chrome extension: `background.js` (actions), `steps/` (service-worker side), `page/` (page side) |
| `spacecall/` | Ally Call: SvelteKit app + SpacetimeDB TypeScript module (`docs/SPACETIMEDB.md`) |
| `demo/` | Demo sites (below) |
| `docs/` | Specs and plans; `docs/integration_plan.md` has the team's change log |
| `handoff/`, `design/` | Specs, eval results and notes between teammates; mascot and design tokens |

---

## Demo sites

| Folder | What |
|---|---|
| `demo/` (Scam Lab) | Fictional scam scenarios: virus pop-up, locked bank account, browser trap, fake technician, and two safe pages. `demo/README.md` has the walkthrough; `python demo/verify_demo.py` checks it. |
| `demo/simple-mode/` | A deliberately cluttered news page ("Heartwarming: Dog and Cat are best friends") for the simple-mode beat. Serve it with `python -m http.server 8001` and open it from `localhost`. |
| `demo/fakebook/` | A fake social feed, a cluttered news article and a gift-card scam page for the video. |
| `demo/capture_site.py` | Saves a real site four ways for slides (original, bigger text, bigger buttons, simple mode). Captures stay local. |

Demo pages can opt in with `<meta name="ally-demo" content="simple-mode">`: the clutter step then also hides
everything tagged `data-demo-clutter`, and `data-delay-ms` holds the "Simplifying" card before the reveal. Real
websites don't have the tag.

---

## Testing

```
cd tests
python timed.py 120 test_config test_registry test_brain test_pagekit test_clicks test_picture   # any modules
python -m unittest discover -p "test_*.py"     # everything (about 1,100 tests)
python timed.py 120 browser.test_page_steps browser.test_tools_e2e browser.test_picture browser.test_clicks browser.test_tabs_pointing browser.test_eye_scroll
```

- The unit tests use a fake Claude API, so they need no key. `timed.py` runs each module in its own process with a
  hard time limit: suites finish in seconds, so a long run means something hangs.
- The browser tests drive Playwright Chromium with the real extension (`pip install playwright; python -m
  playwright install chromium`) and never connect to a running Ally. `scripts/real_sites.py` tries real websites.
- `e2e_chrome.py` and `e2e_voice.py` use port 8765: stop your own Ally first.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Chrome isn't connected" | Load the extension; check the port in its popup matches `agent.port`. |
| A new feature does nothing in Chrome | Reload the extension at `chrome://extensions`, then reload the tab. |
| Hotkeys do nothing | `python run.py doctor`. Linux Wayland: `python run.py gnome-shortcuts`. |
| "offline mode" | Put `ANTHROPIC_API_KEY` in `.env`, check internet, then `python run.py doctor`. |
| Simple mode refuses a page | Sign-in, payment and bank pages are left alone on purpose; Ally says which reason. |
| The eye tracker is off | "Quick calibrate", or "set up my face" again; light on your face helps. |
| Ally uses the robot voice | `python run.py doctor` shows why; `GET /api/voice` shows `fallback_reason`. |
| Can't change a page | Chrome blocks extensions on `chrome://` pages and the Web Store. |

Logs: `logs/ally.log` and `logs/supervisor.log`.

## Privacy and safety

- The server listens on 127.0.0.1 only; the API needs a secret token and checks Host and Origin; the WebSocket only
  accepts Chrome extensions (lock it with `security.allowed_extension_ids`).
- Hands-free listening, eye tracking and face sensing run on your computer: microphone audio and camera video are
  never sent anywhere or saved. Only the text of a request addressed to Ally goes to Claude.
- Claude gets your request and a short context (tab title and site, the app in front, zoom). It gets page text or a
  screenshot only when a tool needs it: describing the screen or a picture, and the AI check after a page change
  (a small before/after screenshot of the tab, never on sign-in, payment or bank pages; `[page_enlarge] ai_check`).
  Screen review for scams is opt-in and budget-capped.
- With Cartesia, text read aloud and the few seconds after a question are sent to Cartesia; nothing is stored, and
  only Ally's fixed prompts are cached.
- Claude can only run registered tools. Clicks that look like paying, sending, deleting or signing out ask first.
- During Ally Call, your screen is shared with the helper you called (`[risk] screen_share`), and they can only
  highlight, tap, scroll or close a tab, never type.
- The microphone and camera stay on while Ally runs; turn them off with `voice.always_listen = false` and
  `gaze.enabled = false`, or say "Ally, turn off eye tracking".

## Hackathon notes

Check the Cal Hacks rules on when code may be written and how to credit AI tools, and say in your submission which
parts you built on this. Ally Call is adapted from SpaceChatDB with its author's permission (`spacecall/README.md`).
