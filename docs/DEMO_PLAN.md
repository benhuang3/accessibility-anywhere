# Ally demo and pitch plan (Cal Hacks 13)

Written Oct 25, ~03:00 PT, from four research passes: past winners, judge and pitch advice, the
official prize list, and what the repo can actually demo. Sources are at the bottom.

**Format (Cal Hacks 13 Devpost):** 3-minute pitch + 2 minutes of judge Q&A at our table, live or
pre-recorded demo. Sponsors also judge asynchronously from the Devpost video.
Venue is the **Palace of Fine Arts, SF**. Submission deadline **9:00 AM PDT Sunday, hard**.

## 1. Fix the track list on Devpost

The Prize Matrix has targets that don't exist here.

| Track | Action | Why |
|---|---|---|
| MLH Best Use of Presage | **Drop** | Not offered at this event |
| MLH Best Use of Gen AI | **Drop** | Not offered (nearest is Best Use of Gemini; we use Claude) |
| MLH Best .Tech Domain | **Drop** | Not offered (only GoDaddy Registry domain prize) |
| Anthropic | Select **"[Anthropic] Claude for Public Benefit"** | Official name; ~$5k + credits. Criteria not posted |
| Most Technically Advanced | It's **"Most Technically Complex Hack"** | Official name |
| Overall, People-First Social Impact, Claude for Public Benefit, Cartesia, Microsoft | **Lead with these** | One story and one demo serve all five |
| Armadin Best Security Hack, Cal Hacks Most Creative | **Add** | Scam warnings fit Security; Creative is free |
| Atlassian Best Agent | Optional | Must name the track in the description |
| Meta: Bringing People Closer | Low priority | $0 cash, a Round 2 invite; video-call flow isn't demo-ready |
| Microsoft | Keep; **ask at the booth** whether Azure is required | No criteria or tech requirement posted |

## 2. The biggest risk: looking like last year's winners

Close precedents: **Elda AI** (eldercare, Cal Hacks 12 Best Use of Claude), **FaceTimeOS** (voice
agent drives the computer, Cal Hacks 12 1st Overall), **Accent** (LA Hacks 2026, helps seniors use
a Mac) and **Companion** (Cal Hacks 11, voice web agent for seniors).

**Our difference, said in the first 20 seconds:** they wait to be asked. Ally notices the struggle
first, offers help out loud, remembers what helped, and steps in calmly on scams.

> **The line we want judges repeating:** "It noticed I was squinting and asked if I wanted the text bigger."

## 3. The 3-minute script

| Time | Beat | What the judge sees |
|---|---|---|
| 0:00–0:20 | **Hook.** One named older adult (a teammate's real relative if possible, no pity framing) + one number (FBI elder-fraud losses, ~$4.9B in 2024: **verify before using**). Then what Ally does, in one sentence. | Printed sign with the one-liner |
| 0:20–1:05 | **Moment 1, the judge is the user.** Judge leans in and squints at a small-text page → *"Having trouble reading? Want me to make it bigger?"* → judge says "yes" → text grows → *"Done. Say undo to put it back."* | Their own face triggers it; dashboard activity log shows "squint 0.45 (fused 0.67 from lean, squint)" |
| 1:05–1:30 | **Moment 2, it learned.** Switch to a pre-trained site → the fix applies by itself, *"…like last time."* | Habit applied without asking |
| 1:30–2:15 | **Moment 3, the wow.** Scam Lab fake tech-support pop-up (`virus.localhost`) → calm, slower spoken warning → judge says "no, it's fine" → *"Are you sure? Scammers often say it's safe to continue."* → "Close it." | Overlay warning panel |
| 2:15–2:40 | **How it works.** Camera stays local (numbers only, never frames) → event bus → coordinator → Cartesia voice (sonic-3 speech, ink-2 listening) → fix. Claude for screen understanding. Never acts without asking; undo always. | One diagram or the dashboard |
| 2:40–3:00 | **Close.** One sponsor line (below), then the one-liner again. | |

**Keep out of the live pitch** (not rehearsed on the demo laptop): AnyDesk / remote-access rule,
family video call, gaze magnifier. Put them in the Devpost video or show them only if asked.

### One-sentence sponsor lines

- **Cartesia:** voice *is* the interface. Ally speaks first, listens for yes / no / "undo" / "stop asking", and uses a calmer, slower voice for warnings.
- **Microsoft:** Windows-native (UI Automation, Magnifier), applies nothing unasked, every change has an undo; we can measure time-to-fix and offer acceptance.
- **Claude for Public Benefit:** named community is older adults with declining eyesight. Claude reads and describes the screen; scam rules are deterministic, not left to Claude; we know where it could go wrong.
- **Social Impact / Security:** older adults are targeted far more by scams; Ally intervenes calmly instead of alarming.

## 4. Roles at the table

- **Presenter:** talks and keeps eye contact. If a judge arrives mid-run, restart from the hook.
- **Driver:** runs the laptop and hotkeys, resets between runs.
- **Spotter:** watches for incoming judges, notes their sponsor, cues the presenter's sponsor line.
- Keep a **60-second version** ready for judges in a hurry.

## 5. Fallback ladder

1. Real signal: webcam squint, real Scam Lab page.
2. Simulated trigger (`python run.py simulate demo`), **saying out loud that it's simulated**. Overclaiming has cost teams prizes.
3. Answer with the overlay buttons or Enter/Esc instead of voice.
4. Backup video, saved on the laptop.

## 6. Q&A prep

- What did you build this weekend, and what did you reuse?
- What's real, and what's simulated?
- Why not just Windows Magnifier? (It has to be found and turned on; Ally notices and offers.)
- Privacy? Frames never leave the camera helper. Voice audio does go to Cartesia, so **don't claim "nothing leaves the computer"**.
- False positives, isn't it annoying? Asks first, needs two signals, "stop asking" snoozes, backs off after a no.
- What happens when the AI is wrong? Undo always; risk rules are deterministic.
- How is this different from Elda or FaceTimeOS? (Section 2.)
- What breaks first? Naming a weakness honestly tends to *raise* scores.

## 7. Gaps to close before judging (priority order)

1. **Real voice in the hall** (Ben W, Ben H): real Cartesia and mic are unverified. Bring a speaker
   aimed at the judge and a wired headset mic. Pre-render the offer prompts so the first offer isn't
   a live network call.
2. **Demo reset key** (Ben H): bypass the 30 s cooldown, clear habits and snoozes, reset the
   one-warning-per-host-per-30-min limit, re-seed the "like last time" site. Without it the second
   judge's run silently does nothing.
3. **Merge `benh/os-layer` into `main`** (Ben H): 7 commits, including the pop-up zoom fix.
4. **Make the reasoning visible** (Ganesh): at minimum the dashboard activity log on a second
   screen facing judges; a Proactive panel if time allows.
5. **Devpost:** video under 3 minutes opening on the squint → bigger text moment, captions burned in;
   a Cartesia README section (setup, features used, limitations; no keys in the repo); corrected
   track list; every teammate added; table number.
6. **Rehearse the full script 5+ times on the demo laptop**, at real resolution and lighting. Bring
   a lamp for even face lighting.

## 8. Table setup

- Second monitor facing judges, mirroring the screen.
- Printed sign: the one-liner + QR code to the Devpost page.
- Small card: "Camera stays on this laptop."
- Webcam at eye height; consistent front light.
- Notifications off, pages pre-opened, phone hotspot ready.

## 9. What past winners had in common

- One named person with a real problem (a grandmother, a Parkinson's relative, a blind cane user).
- A number up front, then the gap ("1.7M legally blind; canes don't tell you where to go").
- One physical or surprising moment the judge experiences.
- The intelligence visible on screen (Lucid Voice: "sophistication only matters if people can see it").
- Reliability over breadth: hardcoded fallbacks, flaky parts cut (FaceTimeOS dropped clicking).
- Prepared for a noisy table: their own speaker, a backup video. Minerva (TreeHacks 2026) moved judges to a quieter spot because their audio was inaudible.

What loses: inaudible audio, a demo that breaks with no fallback, opening with backstory or the
tech stack, vague "what we built this weekend", going over time, overclaiming.

## Sources

- Cal Hacks 13 Devpost: https://cal-hacks-13-0.devpost.com/ · MLH prizes: https://www.mlh.com/events/cal-hacks-13-0/prizes
- Winners: https://devpost.com/software/facetime-macos-ai-agent · https://devpost.com/software/elda-ai · https://devpost.com/software/ted-ai-x8537t · https://devpost.com/software/clearpathai · https://devpost.com/software/raising-cane · https://devpost.com/software/lucid-voice · https://devpost.com/software/foggy · https://devpost.com/software/accent-cdw2qi · https://devpost.com/software/companion-43xfsi
- Write-ups: https://blog.dylanlu.com/cal-hacks-12/ · https://www.evanyu.dev/blog/treehacks-2026 · https://stanforddaily.com/2024/11/20/stanford-students-win-cal-hacks/ · https://cartesia.ai/blog/hacktober
- Pitch advice: https://dev.to/kurbaitaev/what-judges-actually-score-notes-from-a-year-of-hackathon-judging-3p4l · https://info.devpost.com/blog/6-tips-for-making-a-hackathon-demo-video · https://blog.mlh.com/best-practices-for-giving-an-api-demo-at-a-hackathon-01-23-2023 · https://www.ycombinator.com/blog/guide-to-demo-day-pitches/ · https://angelhack.com/blog/10-tips-to-help-you-rock-your-next-hackathon-demo/
