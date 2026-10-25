# Proactive layer: team foundation (Cal Hacks 13.0)

Ally already reacts when you ask it to do something. This layer makes it **notice** when someone is struggling and **offer** help. Everything below already runs end to end. Every piece is a working skeleton, and each owner makes their piece better without touching anyone else's files.

```
 webcam / mouse / keyboard          Chrome extension / URL checks
   ally/senses/  (Hayden)             ally/risk/ + extension/  (Ganesh)
          │  Struggle                         │  Risk
          ▼                                   ▼
        ─────────────  agent.bus  (ally/contracts.py)  ─────────────
                              │
                    ally/proactive.py  (Ben H)
        fuse signals → choose a fix for this app/site → habit? → Offer
                 │                         │                    │
     ally/overlay/ (Ben W)       ally/voice_confirm.py      agent.call_tool(...)
     highlight + Yes/No bubble    (Ganesh) says it,         existing Ally tools
     + warning card               listens for yes/no        (zoom, magnify, read…)
                                                                │
                                              ally/habits.py remembers the yes/no answers
```

## Run it (Windows)

```powershell
git checkout -b foundation
git am 0001-proactive-foundation.patch      # or: git apply proactive-foundation.diff
python -m pip install -r requirements-optional.txt
python run.py agent                          # terminal 1
python run.py simulate demo                  # terminal 2: weak squint + lean fuse into an offer
python run.py simulate answer yes            # or click Yes on the bubble, or say "yes"
python run.py simulate risk scam_popup high "page says call Microsoft support"
python run.py habits                         # what it learned
```

Answer **yes** twice for the same site and the third time Ally applies the fix without asking ("…like last time"). It also does this as soon as you switch to that site.

**Nobody needs a webcam or another teammate's code to work.** Use `simulate` to send any event.

## The contracts (ally/contracts.py): don't break these

| Message | Fields |
|---|---|
| `Struggle` | `kind` (squint, lean, rage_click, hesitation, retype, fused), `confidence` 0–1, `target{app, site, window, x, y, box}`, `source`, `detail`, `ts` |
| `Risk` | `kind` (scam_popup, untrusted_site, remote_access, payment_field), `severity` (low, medium, high), `evidence`, `url`, `app` |
| `Offer` | `prompt`, `accommodation{key, label, tool, args}`, `context`, `highlight`, `timeout`. **`offer.resolve("yes"/"no", by)`: the first answer wins.** |

Bus topics are `struggle`, `risk`, `answer`, `offer`, `offer_result`, `applied`, `risk_warning` and `context`. Subscribe to `*` to receive all of them.

HTTP: `POST /api/event` takes `{"type":"struggle"|"risk"|"answer", ...}`. `GET /api/habits` lists what Ally learned, and `POST /api/habits {"forget":true}` wipes it. Both need the `X-Ally-Token` header. The Chrome extension can post risks here.

If you need a new field, add it with a default value and post in the group chat.

## Who owns what

| Person | Files | First tasks |
|---|---|---|
| **Hayden**: vision | `ally/senses/` | Download [face_landmarker.task](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task) into `ally/senses/`, then set `senses_vision = true`. Tune `SquintDetector` and `LeanDetector` on real faces, with glasses and in different lighting. Behavior thresholds live in `behavior.py`. Optional and timeboxed: gaze, which goes into `target_here()` in place of the cursor. |
| **Ben H**: brain and integration | `ally/proactive.py`, `ally/habits.py`, `ally/contracts.py`, `agent.py` wiring | Tune `pick_accommodation` and the prompts. Add per-site habit options beyond zoom (reader view, min font). Merge branches at 1 PM, 6 PM and 10 PM. |
| **Ganesh**: voice, risk and pitch | `ally/voice_confirm.py`, `ally/risk/`, `extension/` risk scripts | Write an extension content script that sends page text and URL, posts `/api/event` risk, and runs `check_text` and `check_url`. Add a remote-access process check (`check_process_names`). Use Cartesia voice for prompts. |
| **Ben W**: UI | `ally/overlay/`, `dashboard/` | Run `python -m ally.overlay` for a preview. Make it look great at 200% DPI, add an animated highlight and a "Proactive" dashboard panel (use `/api/status` → `proactive` and `/api/habits`). |

## Settings (config.toml → `[proactive]`)

`enabled`, `overlay`, `voice_confirm`, `senses_behavior`, `senses_vision`, `camera_index`, `min_confidence` (0.6), `fusion_window_seconds` (4), `cooldown_seconds` (30), `offer_timeout_seconds` (8), `auto_apply_after` (2), `context_poll_seconds` (1.5). Changes apply live.

## Tests

```bash
cd tests && python -m unittest test_config test_registry test_brain test_network test_runtime test_proactive
```

There are 96 tests in total, including 21 new ones covering contracts, the bus, habits, the full offer flow, the detectors, risk rules, yes/no parsing and the HTTP wiring. Add a test whenever you change a threshold.

## What isn't done yet (honest list)

- The overlay has been rendered and checked on Linux and Xvfb, but **not yet on Windows**. Check that `-transparentcolor` click-through works, along with DPI scaling and multi-monitor setups.
- Vision has only been checked with synthetic numbers. It needs real-camera tuning.
- Voice confirm reuses Ally's recorder. If someone presses the voice hotkey while an offer is open, the two will compete for the mic.
- Risk rules are starter regex and URL heuristics. No extension content script exists yet.
- Habits are only keyed by site, or by app outside the browser. Nothing is learned when the context is unknown.
