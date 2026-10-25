# Ally: an always-on, OS-level accessibility agent

Ally runs in the background on your computer. You talk to it, type to it, or press a hotkey, and it
changes the screen and the web for you: bigger text, high contrast, reader view, read aloud, screen
magnifier, color filters, and anything else you teach it.

- **Hands-free**: just say "Ally, make it bigger". Ally is always listening for its name, and the speech
  is turned into text on your computer.
- **Knows where you're looking**: "Ally, magnify what I'm looking at" zooms the whole screen around the
  spot your eyes are on, using the webcam. The magnified view then follows your eyes.
- **Works everywhere**: global hotkeys and screen tools work in every app; a Chrome extension handles web pages.
- **Easy to change**: settings, commands, hotkeys and page styles live in one file (`config.toml`) that
  reloads the moment you save. New tools are one Python function in the `plugins/` folder.
- **Always on**: auto-restart on crash or freeze, start at login, offline mode when there's no internet,
  the extension reconnects by itself, and a panic button undoes everything.
- **No required packages**: the core is pure Python 3.11+. Optional packages add hotkeys, voice and screenshots.

---

## Quick start (about 5 minutes)

1. **Python 3.11 or newer.** Check with `python3 --version`. **Windows:** type `python` wherever this
   README says `python3` (the python.org installer adds `python`; `python3` and `py` often don't exist).
2. **Get the code** and open a terminal in this folder.
3. **Add your API key**: copy `.env.example` to `.env` and paste your Anthropic key after `ANTHROPIC_API_KEY=`.
   (No key? Ally still runs in offline mode with simple commands.)
4. **Install the optional extras** (recommended):
   ```
   python3 -m pip install -r requirements-optional.txt
   ```
5. **Start Ally**:
   ```
   python3 run.py
   ```
   It says "Ally is ready". The dashboard is at http://127.0.0.1:8765.
6. **Add the Chrome extension**: open `chrome://extensions`, turn on **Developer mode**, click
   **Load unpacked**, and pick the `extension` folder. The Ally icon shows no badge when it's connected
   and "off" when the agent isn't running.
7. **Check everything**: `python3 run.py doctor` lists what works and how to fix what doesn't.
8. **Start at login** (optional): `python3 run.py install` (undo with `python3 run.py uninstall`).

### Per-system notes

| System | What to do |
|---|---|
| **Windows** | Use 64-bit Python and run commands with `python`, e.g. `python run.py doctor`. The screen magnifier and color filters use the Windows Magnification API directly; if the Windows Magnifier app is open, Ally closes it and takes over. Speech uses the built-in Windows voices ("Microsoft David Desktop", "Microsoft Zira Desktop"; set `speech.voice`) and stays loaded, so only the first sentence after start waits a few seconds. Hotkeys ignore key presses sent by other programs, so test them by pressing them yourself. "Read the selection" (Ctrl+Alt+R) copies once you let go of Ctrl+Alt and then puts your clipboard text back. |
| **Linux** | Screen magnifier, color filters and text size need GNOME. Install `espeak-ng` or `speech-dispatcher` for speech and `xclip` (X11) or `wl-clipboard` (Wayland) for reading selected text. On Wayland, Ally's own hotkey listener only sees X11 apps, so run `python3 run.py gnome-shortcuts` (turns your `[hotkeys]` into GNOME shortcuts) and set `hotkeys_enabled = false` under `[agent]`. |

---

## Using Ally

**Say or type** (just talk, or use the dashboard box, extension popup, voice hotkey, or `python3 run.py send "..."`):
"this page is hard to read", "make it bigger", "read this to me", "high contrast", "reader mode",
"summarize this page", "what's on my screen?", "invert colors", "reset".

### Hands-free voice

Start a request with **"Ally"** (or end with it): "Ally, zoom in", "Hey Ally, read this page",
"Make it bigger, Ally". Say just "Ally" and it beeps; then say the request. After Ally answers, you
have a few seconds for a short follow-up without the name ("a bit more", "smaller").
While Ally is talking, say **"Ally, stop"**.

- Needs `pip install faster-whisper`. The speech model downloads once (base.en, about 145 MB) and runs
  on your computer, so audio never leaves it. Windows shows the microphone as in use while Ally runs.
- Settings under `[voice]`: `always_listen`, `wake_words`, `follow_up_seconds`, and `whisper_model`
  (`tiny.en` is faster, `small.en` more accurate).

### Spoken offers and yes/no answers (Cartesia)

Ally speaks first: "Want me to make this bigger?" Then it listens for the answer. The on-screen Yes / No
buttons always work too, and the first answer wins. Code: `ally/voice_confirm.py`.

```python
from ally.voice_confirm import say, ask_yes_no
say("Done. Say undo to put it back.")
ask_yes_no("Want me to make this bigger?")   # -> "yes" | "no" | "timeout"
agent.confirm.confirm_offer(offer)           # asks offer.prompt, calls offer.resolve(answer, by="voice")
agent.confirm.warn({"kind": "scam_popup", "severity": "high"})   # "This pop-up may be a scam. Close it?"
```

- **Voice**: Cartesia Sonic (`sonic-3.6`, then `sonic-3`), calm emotion, a little slower for questions
  and slower still for risk warnings, with a short pause before the question. Put `CARTESIA_API_KEY` in
  `.env` and a voice in `config.toml` (`[speech] cartesia_voice_id = "Tessa"`; a name or an ID).
  `python run.py voices` lists voices; `python run.py voice-test Tessa Leo Marian Dana --play` plays
  Ally's prompts in each and prints time to audio and words per minute (aim for about 150-160).
- **Fallbacks**: Cartesia model -> fallback model -> the computer's built-in voice. After a network
  failure Ally uses the built-in voice for 30 s instead of waiting on every sentence. Ally's fixed
  prompts are rendered with Cartesia at start and cached on disk, so they keep the Cartesia voice
  offline. Answers are heard with Cartesia Ink, then offline Whisper; with neither, the buttons decide.
- **Safety**: silence or an unclear answer ("yeah, no", "maybe", "that's fine") never counts as yes.
  An unclear answer gets one re-ask in other words, then "You can tap Yes or No". "What?" and
  "slower" replay the question more slowly (and "slower" is kept as a habit). Ally only listens after
  it stops talking, so it never answers itself, and it borrows the always-on listener's microphone
  rather than opening a second one.
- **Speed**: a clear "yes"/"no" is accepted after a 0.45 s pause instead of the full one (a bare
  "yeah" or "sure" still waits, since it may be "yeah... no"). Timings go to `logs/voice_latency.jsonl`
  and `GET /api/voice`.
- **Try it**: `python run.py say "Want me to make this bigger?"`, `python run.py ask "Want me to make
  this bigger?"`, `python run.py answer yes`, `python run.py warn scam_popup`. They use the running
  agent if there is one, otherwise they run on their own.
- Settings under `[confirm]` (listen time, re-ask, speeds, eager answers) and `[speech]`.

### Eye tracking

1. Say **"Ally, set up my eyes"** (or "calibrate my eyes"). About 45 seconds: Ally checks
   that it can see your face, shows 9 dots, then asks you to sit back a little and lean in a little
   (5 dots each, so head movement is part of the setup), then 5 check dots it did not train on.
   Esc cancels. `[gaze] calibration_poses = 1` is the 9 dots only, about 30 seconds. Do this
   again if you move your laptop or chair a lot.
2. Say **"Ally, what am I looking at"** for the area of the screen. On Windows, Ally also names the
   button, link, or text under that spot. **"this"**, **"that"**, and **"here"** use the same tool.
3. Say **"Ally, magnify what I'm looking at"** (or "magnify here"), or press `<mod>+G`. The screen
   zooms around where you looked (1.5x unless you name a level) and becomes a reading aid: as you read
   along a line, the view glides right at your pace and keeps the words you are reading near the
   middle. At the end of the line, look back to the left and it returns to where the line began
   (and brings the next line toward the middle). Looking around without reading leaves it still,
   and looking near an edge pans that way, calmly, after a moment. "Ally, help me read this" does the
   same. Say **"follow my eyes slower"** or **"follow my eyes faster"** to change how eagerly it
   moves (saved as `[gaze] pan_speed`, default 0.85). It never pans faster than
   `[gaze] max_pan_speed` (default 0.35 screen widths a second), and looking near an edge waits
   about half a second first. Move the mouse and the view follows the mouse again. Say
   "follow my eyes" / "follow the mouse" to switch, and "reset" or "magnifier off" to stop.
   Say **"show where I am looking"** for a soft circle on the spot the tracker is using (click-through;
   it does not get in the way), and **"hide the eye dot"** to remove it. `[gaze] show_dot` remembers
   that, and it is off until you ask.
4. A yes or no can be answered by looking. Ally shows the choices as large cards (two choices: the
   left half and the right half). Look at one for a moment. That look teaches the tracker, using the
   centre of the card you chose.
5. Say **"Ally, quick calibrate"** (or "check my eye tracker") when it is off: 5 quick dots, about 6 seconds,
   re-tune your saved setup. A full setup is needed only once, or after a new screen or camera.

The check uses plain words:

- **Good**: about 150 pixels or less, and the areas of the screen were right (8 of 9 on a full grid;
  with 5 dots, all 5).
- **Okay**: the area is right often enough to trust left, right, or the 3x3 grid, not an exact spot.
- **Poor**: too many areas were wrong. The next sentence is the likely cause (light, distance, or
  glasses glare).

If the eyes aren't set up, Ally can't see your face, your look doesn't settle, or the estimate is
too unsure, Ally uses the mouse pointer and says so.

- Needs `pip install mediapipe opencv-python` and a webcam. v5 also needs `pip install onnxruntime` and
  one download, `python scripts/get_gaze_models.py`, which puts `mobileone_s0_gaze.onnx` in `state/models`.
  The face model downloads once (3.7 MB). Video is processed on your computer and never saved; only the
  calibration (a few numbers) is kept, in `state/`. Mouse clicks can quietly improve the estimate
  (`gaze.click_learning`, on by default). What they teach is saved next to the calibration and
  restored when Ally starts. A new setup starts fresh. The file is feature numbers, screen points,
  and times, not pictures. The click stays on this computer.
- `[gaze] features` defaults to `v5`: the v4 head model plus MobileGaze yaw and pitch from `mobileone_s0`.
  If that file or onnxruntime is missing, setup and tracking use v4 and say so. A new setup uses whichever
  you set. `max_radius_px` (default 250) is when Ally stops trusting the eyes and uses the pointer.
- The camera stays on while Ally runs. Say "Ally, turn off eye tracking" (until Ally restarts), or set
  `gaze.enabled = false`.
- A webcam is far less precise than a dedicated eye tracker. Areas of the screen are the useful part.
  Light on your face helps. `python run.py doctor` reports how old the calibration is, the check, the
  feature set, the camera index, and whether `mobileone_s0_gaze.onnx` and onnxruntime are ready.
- Exact centering and following the eyes are Windows features. On Linux, Ally moves the
  pointer to where you look and zooms there, since those magnifiers follow the pointer. The control
  name under the gaze point is Windows-only; other systems get the screen area.

### Noticing struggle (senses)

Ally watches for signs that you're having a hard time, so it can offer help before you ask:

- **Face** (webcam): squinting and leaning toward the screen, each measured against *your* resting face.
  Frames where you blink, smile or laugh, wink, turn away, or sit in the dark are ignored.
  Calibration checks squint stability; if lighting, glare, or uneven readings make it unreliable,
  squint sensing turns off while lean and input sensing stay available. Recalibrate with **Ctrl+Alt+Shift+C**.
- **Mouse and keyboard**: clicking the same spot again and again, deleting and retyping, and the pointer
  resting a long time without a click over a clickable control. Only *that* a key was pressed is counted,
  never which key.

One signal alone doesn't count. Two different signals within a few seconds (for example a squint
plus leaning in) become one **struggle** event, `{kind, confidence, target {app, site, window, x, y,
box}, source, detail, ts}`, that goes to the event log, to `agent.on_struggle(dict)` and to
`agent.senses.subscribe(callback)`. Code is in `ally/senses/`.

- Say **"Ally, set up face sensing"** once: 15 seconds of reading normally. Without it, Ally learns
  your resting face from the first 20 seconds it sees you.
- If Ally offers help and you say no, it quiets those signal types for `decline_seconds` (default 90,
  configurable from 30 to 600 seconds). Call `agent.senses.answer(struggle, "yes" or "no")` to record feedback.
- Set `[senses].post_url` to an endpoint to opt in to POSTing struggle events to `/api/event`; it is
  empty by default, so no events are sent.
- "Ally, stop watching me" or "turn off the camera" switches sensing off until Ally restarts. Or set
  `senses.vision` / `senses.input` to false.
- Live tuning: `python run.py tool senses_hud` opens a window with the camera picture, every signal,
  its baseline and the gates; the thresholds live in `[senses]` in `config.toml` and apply on save.
- No webcam? `python run.py tool simulate_struggle '{"kind": "demo"}'` shows a timed demo: weak squint,
  then weak lean 1.5 seconds later (or use `squint`, `lean`, `rage_click`, `hesitation`, `retype`).
- The camera is opened only by the eye tracker's helper process, which sends the agent a few
  numbers per frame (like a squint score). Frames stay in memory there and are thrown away;
  `tests/test_senses.py` fails if camera code imports networking or writes images or video.

**Default hotkeys** (`<mod>` = Ctrl+Alt):

| Keys | Action |
|---|---|
| `<mod>+V` | Talk to Ally (press again to stop listening) |
| `<mod>+R` | Read the selected text aloud (any app) |
| `<mod>+S` | Stop talking |
| `<mod>+=` / `<mod>+-` | Bigger / smaller (the web page if Chrome is in front, else the whole screen) |
| `<mod>+0` | **Panic button**: undo every visual change |
| `<mod>+M` | Screen magnifier on/off |
| `<mod>+G` | Magnify where you're looking (eye tracker) |
| `<mod>+E` | Describe what's on the screen |
| `<mod>+1` | Reading mode (an example command) |
| `<mod>+A` | Open the dashboard |

The Chrome extension also has its own shortcuts that work even when the agent is off:
Alt+Shift+Period (bigger), Alt+Shift+Comma (smaller), Alt+Shift+0 (reset), Alt+Shift+R (read page).

**Command line**

```
python3 run.py              start (with auto-restart)      python3 run.py status     what Ally is doing
python3 run.py doctor       check setup                    python3 run.py stop       stop Ally
python3 run.py send "..."   send a request                 python3 run.py restart    restart Ally
python3 run.py tool NAME '{"arg": 1}'   run one tool       python3 run.py check      validate config.toml
python3 run.py agent        run without auto-restart (development)
python3 run.py gnome-shortcuts          Linux/GNOME: hotkeys as GNOME shortcuts (gnome-shortcuts-remove undoes it)
```

---

## Make it do anything

### 1. Change settings: `config.toml`

Created on first start, fully commented. Edit it in any editor or in the dashboard's Settings box.
Changes apply within a second. If you make a mistake, Ally keeps the last good settings and shows the
exact problem (line, key, and what it expected). Highlights:

- `[brain]` `model`, `effort`, and `system_prompt` (how Ally behaves).
- `[hotkeys]` any key combo to any tool, command, or sentence.
- `[profile]` defaults applied at start and on every page (zoom, minimum text size, styles, magnifier).
- `[page_styles]` CSS you can apply to any page. Each name becomes an option of `restyle_page`.
- `[tools] disabled` turns tools off.

### 2. Add a command (no code)

```toml
[commands.morning_news]
description = "Open the news in large, high-contrast text and read the top story."
phrases = ["morning news", "read me the news"]
hotkey = "<mod>+n"
steps = [
  { open = "https://text.npr.org" },
  { wait = 2 },
  { tool = "set_page_zoom", args = { zoom = 1.5 } },
  { tool = "restyle_page", args = { style = "high_contrast" } },
  { ask = "Read the first headline and its summary aloud." },
]
```

Step types: `tool` (+ `args`), `say`, `ask` (a request for Claude, which can use tools), `open` (URL or
file), `keys` (e.g. `"<ctrl>+c"`), `wait` (seconds), `notify`, and `shell`. `shell`, `open` and `keys`
can be different per system: `{ windows = "...", linux = "..." }`.
Each command is also a tool Claude can choose by itself (set `allow_ai = false` to prevent that).

### 3. Add a tool (Python)

Drop a file in `plugins/`. It loads instantly and reloads when you save. A broken file never stops Ally.

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

- The **docstring** is what Claude reads to decide when to use the tool. Say what it does and when.
- **Type hints** become the input schema (`str`, `int`, `float`, `bool`, `list[str]`, `Literal[...]`,
  `Annotated[type, "description"]`). Parameters with defaults are optional.
- **`ctx`** gives you everything: `ctx.browser.call("getText")`, `ctx.os.magnify(2)`, `ctx.say("hi")`,
  `ctx.ask("question", image=png_bytes)`, `ctx.run("other_tool", ...)`, `ctx.state`, `ctx.config`.
- `@tool(ai=False)` hides a tool from Claude (hotkeys, commands and the dashboard can still use it).

See `plugins/example_tools.py` and `plugins/plain_language.py` (rewrites any page in plain words).

### 4. Add a browser action

Page actions live in `extension/background.js` in the `ACTIONS` object. Add a function there, reload the
extension at `chrome://extensions`, then call it from a tool with `ctx.browser.call("yourAction", x=1)`.

---

## Built-in tools

| Web pages (Chrome) | Whole screen / system | Other |
|---|---|---|
| `set_page_zoom`, `set_min_font_size`, `restyle_page`, `reader_view`, `read_page`, `get_page_text`, `scroll_page`, `browser_navigate`, `open_website`, `remember_site_style` | `magnify_screen`, `magnify_where_i_look`, `calibrate_eye_tracker`, `magnifier_follow`, `eye_tracking`, `color_filter`, `set_text_size`, `describe_screen`, `read_selection` | `reset_all`, `remember_preference`, `forget_preferences`, `apply_profile`, `set_speech_rate`, plus hotkey-only: `zoom_in`, `zoom_out`, `zoom_to`, `toggle_magnifier`, `listen`, `stop_speaking`, `open_dashboard`, `show_help` |

---

## How it stays on

| Problem | What Ally does |
|---|---|
| Agent crashes | The supervisor (`run.py start`) restarts it: 1 s, 2 s, 4 s ... up to 30 s apart |
| Agent freezes | `/health` checked every 5 s; 3 misses in a row and it's restarted |
| Computer restarts | `run.py install` starts Ally at login (Run key on Windows, autostart on Linux) |
| No internet / no key / API down | Offline rules handle common requests; commands and hotkeys keep working |
| Chrome closed or agent restarted | The extension reconnects by itself (backoff, plus a 30 s alarm that wakes it) |
| Bad config edit | Last good settings stay active; the error is shown |
| Broken plug-in | That file is skipped (old version kept); everything else runs |
| Hotkey hook crashes | It runs in its own process and is restarted; it can't take Ally down |
| Camera / eye tracker crashes | Same: its own process, restarted (up to 4 times in 2 minutes) |
| Microphone unplugged or blocked | Hands-free listening shows the problem and tries again every 5 s |
| A tool hangs | Every tool has a timeout (15 s default) |
| Something looks wrong on screen | `<mod>+0` or "reset" undoes every visual change |

---

## How it works

```
 you: voice / hotkey / typing
            |
   +--------v---------------------------- Ally agent (Python, run.py) -----------------------+
   |  hotkey helper (own process) ---> brain: 1) fast path: commands + simple rules          |
   |  dashboard + JSON API  -------->         2) Claude tool use (tools = registry)          |
   |  CLI (run.py send)     -------->         3) offline rules if Claude is unreachable      |
   |                                     |                                                   |
   |                        tool registry: built-in tools + plugins/*.py + [commands]        |
   |                          |                         |                       |            |
   |                  OS adapter (Win/Linux)       speech (OS voice /     Chrome bridge       |
   |                  magnifier, colors, keys,     Cartesia)              (WebSocket)         |
   |                  selection, screenshots                                   |             |
   +---------------------------------------------------------------------------|-------------+
          supervisor (restarts it)                                             |
                                                         Chrome extension: zoom, CSS styles,
                                                         reader view, page text, read aloud
```

| File | What it does |
|---|---|
| `run.py`, `ally/cli.py` | Commands |
| `ally/agent.py` | Wires everything together |
| `ally/brain.py`, `ally/claude.py`, `ally/rules.py` | Decides what to do (Claude + offline rules) |
| `ally/registry.py` | `@tool` decorator, schemas, plug-in loading, safe calls with timeouts |
| `ally/config.py`, `ally/default_config.toml` | Settings, validation, hot reload |
| `ally/macros.py` | `[commands]` from the config |
| `ally/tools/` | Built-in tools |
| `ally/platforms/` | Windows and Linux adapters |
| `ally/server.py`, `ally/websocket.py`, `ally/bridge.py` | Dashboard, API, extension link |
| `ally/hotkeys.py`, `ally/hotkey_helper.py` | Global hotkeys in a separate process |
| `ally/speech.py`, `ally/voice.py` | Talking, push-to-talk and offline speech-to-text |
| `ally/listener.py` | Hands-free: always listening for "Ally, ..." |
| `ally/gaze.py`, `ally/gaze_helper.py`, `ally/gaze_model.py` | Eye tracking: webcam process, calibration, gaze math |
| `ally/supervisor.py`, `ally/autostart.py`, `ally/doctor.py` | Always-on pieces |
| `extension/` | Chrome extension (Manifest V3) |
| `dashboard/` | Control panel web page |

### API details used (checked October 2026)

- Claude Messages API with tool use. Default model `claude-haiku-5-5` with `output_config.effort = "low"`
  and thinking off for speed. Forced tool choice isn't used because Sonnet 5.5 and Opus 5.5 reject it.
  If a model rejects a speed setting, Ally retries without it and remembers.
- Screenshots go to Claude inside a `tool_result` (images are allowed there), shrunk to 1568 px when Pillow is installed.
- Chrome: `tabs.setZoom`, `fontSettings.setMinimumFontSize`, `scripting.insertCSS/removeCSS/executeScript`,
  `tts`, `alarms` (30 s minimum), and a WebSocket that keeps the service worker alive (Chrome 116+).
- Windows: `MagSetFullscreenTransform` (1.0-4096.0, offsets in unmagnified pixels) and
  `MagSetFullscreenColorEffect` (Microsoft's grayscale matrix). GNOME: `org.gnome.desktop.a11y.*` keys.
- Cartesia: `POST /tts/bytes` (model `sonic-3.6`, fallback `sonic-3`; `generation_config` speed 0.6-1.5, volume,
  emotion), `GET /voices`, and `POST /stt` (model `ink-whisper`) with header `Cartesia-Version: 2026-08-14`.

---

## Testing

```
cd tests
python3 -m unittest discover -p "test_*.py"   # 154 tests, ~35 s
python3 e2e_chrome.py   # real Chromium + extension + agent (needs: pip install playwright; python -m playwright install chromium)
python3 e2e_voice.py    # the real agent asks 21 spoken questions against a fake Cartesia, microphone and speakers (~4 min)
python3 voice_phrases.py   # yes/no understanding on 184 labeled answers (accuracy, false-yes count)
```

The unit tests use a fake Claude API, so they need no key. The end-to-end test uses port 8765, so stop
your own Ally first.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Chrome isn't connected" | Load the extension (step 6). Check the extension's port in its popup matches `agent.port`. |
| Hotkeys do nothing | `python3 run.py doctor`. Linux Wayland: `python3 run.py gnome-shortcuts`. |
| Ctrl+Alt hotkeys type symbols | On some European keyboards Ctrl+Alt = AltGr. Pick other keys in `[hotkeys]`. |
| "offline mode" | Put `ANTHROPIC_API_KEY` in `.env`, check internet, then `python3 run.py doctor`. |
| Port 8765 in use | Change `agent.port` in config.toml and in the extension popup (Settings). |
| Ally uses the robot voice | `python3 run.py doctor` shows why (no key, no voice, or Cartesia unreachable). `GET /api/voice` shows `fallback_reason`. |
| Spoken answers are ignored | `python3 run.py doctor` > "Spoken questions". Needs `sounddevice` and Cartesia or faster-whisper; the buttons always work. |
| Can't change a page | Chrome blocks extensions on `chrome://` pages and the Web Store. Try a normal site. |

Logs: `logs/ally.log` and `logs/supervisor.log`.

## Privacy and safety

- Spoken yes/no answers: with Cartesia speech-to-text, only the few seconds after a question (the
  answer window) are sent to Cartesia; nothing is stored. The latency log has timings, never what was
  said. Offline Whisper keeps the answer on the computer. Text read aloud is sent to Cartesia to be
  spoken but is never cached; only Ally's own fixed prompts are cached (in `cache/voice`).
- The server listens on 127.0.0.1 only. The API needs a secret token, checks the Host header (blocks DNS
  rebinding) and the Origin; the WebSocket only accepts Chrome extensions (lock it to your extension ID
  with `security.allowed_extension_ids`).
- Claude only gets: your request, a short context (tab title and site, the app in front, current zoom),
  page text or a screenshot only when a tool asks for it. After Ally makes a web page bigger or simpler, it
  sends Claude a small screenshot of the visible tab from before and after, to check the result (never on
  sign-in, payment or bank pages). Turn this off with `ai_check = false` under `[page_enlarge]`. Turn off `share_tab_info` / `include_active_app`
  in `[brain]` if you prefer.
- Claude can only run registered tools. It can't type arbitrary shell commands; `shell` steps are fixed in your config.
- The dashboard's mic button uses Chrome's speech recognition, which sends audio to Google.
- Hands-free listening and eye tracking run on your computer: microphone audio and camera video are never
  sent anywhere or saved. Only the text of a request addressed to Ally goes to Claude (as with typing).
  The microphone and camera stay on while Ally runs; turn them off with `voice.always_listen = false` and
  `gaze.enabled = false`, or say "Ally, turn off eye tracking".

## Scam protection

Ally looks for fake virus warnings, lookalike bank addresses, card forms on unfamiliar sites,
remote-control tools running while a bank page is open, and pages that try to trap you.
It also warns when a remote tool is running and someone opens Event Viewer or another system tool used for fake scans.
Local rules check first. If you turn on screen review, AI can look at a flagged screen for a second opinion.
A calm panel explains the concern and asks before closing a page or remote tool. Tap Yes or No,
or press Enter / Esc; the current risk prompts cannot hear spoken answers.
When a remote tool and bank page are open together, No (continue) waits for a 30-second countdown; Yes (close) works at once (`ally/risk/wire.py`).

High warnings can also offer Call or Tell a trusted person. With a standing Google Meet link configured,
Call opens the room in Chrome and sends the helper a phone push with a Join button; Tell sends only the push.
Set `trusted_contact`, `contact_topic` and optionally `contact_call_url` under `[risk]` to enable it.

The saved screen eval scored **9/14 with rules alone, 13/14 with AI**, including hidden-text instructions
trying to make the AI call a scam safe ([screen eval](handoff/results/guardian_eval.txt)).
A rules-only sweep warned on **3 of 500 sites (0.6%), none high** ([site sweep](handoff/results/fp_sweep.txt)).
Our own rules set has **25 scam texts, 25 benign texts, 10 lookalike addresses and 10 real addresses**
([test_risk_eval](tests/test_risk_eval.py)). These small, self-written checks do not cover every scam.

Screen review is **off by default** and has daily, hourly and per-page spend caps. A review sends a screenshot
of the whole primary screen to Anthropic. Contact alerts never include the page address or page content.
ntfy topics are only as private as their name, and the call link is a standing room.
See [the risk handoff](docs/RISK_HANDOFF.md) for setup, evidence and limitations.

## Hackathon notes

Check the CalHacks rules on when code may be written and how to credit AI tools, and say in your
submission which parts you built on this.
