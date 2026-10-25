# Ally Scam Lab

A local, fictional demo of Ally's scam protection. The site uses plain HTML,
CSS and JavaScript, system fonts, and local assets. Forms neither save nor send
details. The support button does not download or install anything.

From this folder, run:

```powershell
python -m http.server 8000
```

Open **http://localhost:8000/** in Chrome with the Ally extension connected to
your existing agent. The static server alone displays the pages; the real Ally
agent supplies the warnings. This demo does not start Ally.

Use the landing-page links for the walkthrough. They open separate loopback
hosts such as `virus.localhost:8000`, all served by the same local server. This
keeps Ally's per-host warning cooldown from swallowing the next scenario.
Chromium resolves `.localhost` locally; an internet connection is unnecessary.
Use `localhost`, rather than `127.0.0.1`: Ally's address rules flag raw IPs.
Revisiting the same scenario can still be snoozed by Ally after a response; a
fresh site tab does not clear that agent-side state.

## Five-beat walkthrough

1. **Virus alert** — Open “The virus alert.” The large, positioned AsterShield
   pop-up combines an alarming claim, a fictional support number, and a request
   to call. Expect `scam_popup HIGH`. Use Ally's on-screen response controls.
2. **Bank, remote tool, and call** — Have an already installed AnyDesk or Quick
   Assist running before opening “The locked account.” Card/PIN/security-code
   fields and the account-lock copy produce `payment_field MEDIUM` and
   `scam_popup MEDIUM` in the page-only scan. With the remote tool present,
   the live agent prioritizes `remote_access HIGH` from the joined rule.
   Use **Call** in Ally's actual warning if a trusted person and standing call
   link are already configured. The demo page does not simulate a successful
   call. Do not enter real banking details.
3. **Browser trap** — Open “The page that won't let go” and click **Activate
   secure viewing**. It pushes history entries, re-adds itself on Back,
   requests fullscreen, prevents the right-click menu, and repeats real alert
   dialogs. The initial text scan is `scam_popup MEDIUM`; the visible overlay
   raises it to `HIGH`. The production behavior counters separately produce
   `scam_popup MEDIUM` through `RiskWire.on_page`. Dismiss dialogs to see Ally.
   To stop the loop, dismiss the current native dialog, then press **Esc**
   between dialogs, click **Stop demo & leave**, or use the **← Scam Lab** pill.
   Browsers can decline fullscreen or suppress repeated native dialogs.
4. **Fake scan** — Keep the remote tool running and open “The ‘helpful’
   technician.” This page itself scores `scam_popup MEDIUM`. Allow the agent
   to observe the remote session, then open the real **Event Viewer** from
   the Start menu. It must be newly opened after that baseline, not left
   running from an earlier rehearsal. Expect `remote_access HIGH` from
   `risk.fakescan` after the next process poll. The illustrated event log on
   the page cannot create a real OS process or trigger that rule.
5. **Safe comparison** — End on “A little everyday shopping” or “Reading about
   the risk.” Both must stay quiet. The shop reserves candles for payment at
   pickup and has no card fields: a card-entry checkout on an unfamiliar
   localhost host would correctly trigger the current payment rule. The news
   article discusses scams without a phone number or a demand to contact
   support. Keep Event Viewer closed and end the remote session first so an
   unrelated process warning does not interrupt the safe comparison.

All businesses, reporting, and phone numbers on the pages are fictional.
The real software names above identify the presenter's existing tools; the
demo provides no installers or external links.

## Verify and capture screenshots

From the repository root:

```powershell
python demo/verify_demo.py
```

Or from this folder:

```powershell
python verify_demo.py
```

The verifier needs Python 3.11+ and an installed Chrome, Edge, or Chromium.
It uses only Python's standard library; Playwright is not required. If a
Playwright-managed Chromium is installed, it can also be discovered. Pass
`--browser "C:\path\to\chrome.exe"` if browser discovery needs help.

The verifier launches its own headless browser and an ephemeral loopback
static server. It never starts `run.py`, uses port 8765, reads credentials or
configuration, inspects real running processes, or calls an AI service.
Browser sandboxing is disabled only in this isolated verification browser
so it can run within the restricted Windows workspace; its temporary profile
is confined to `demo/` and removed when the browser closes.

`collect-page.js` copies the field, visible body-text, overlay-area,
fullscreen, and telephone-link collection logic from `extension/risk.js`.
The production `extension/risk-main.js` is injected before page scripts, so
the trap's behavior signals come from real browser actions, not invented
counters. Typed form values are never part of a snapshot.

Checks cover every HTML page at 1440×900 and 390×844, actual repeating dialogs,
Back trapping, Esc release, local-only form completion, absence of external
requests and JavaScript errors, and `RiskWire.on_page`. Joined and fake-scan
checks use synthetic AnyDesk/Quick Assist/Event Viewer process snapshots,
with a fresh risk wire per case. They do not run those tools or place a call.

Outputs:

- `verification.json`: exact scan results and integration checks.
- `page-snapshots.json`: the rendered dictionaries passed to `scan_page`.
- `shots/index-light-1440x900.png` and `shots/index-dark-1440x900.png`.
- `shots/index-light-390x844.png` and `shots/index-dark-390x844.png`.
- Additional full-page captures for inspecting the complete layout.

Use `--no-screenshots` to check behavior without refreshing image files.
Light and dark follow the system preference; reduced motion disables the
landing page's drifting background and hover movement. Keyboard focus rings,
18px body text, and opaque text tints keep the glass panels readable.

## Checked rule results

| Page | `scan_page` result |
| --- | --- |
| `index.html` | none |
| `virus.html` | `scam_popup HIGH` |
| `bank.html` | `scam_popup MEDIUM`, `payment_field MEDIUM` |
| `trap.html`, before activation | `scam_popup MEDIUM` |
| `trap.html`, after activation | `scam_popup HIGH` |
| `support.html` | `scam_popup MEDIUM` |
| `shop.html` | none |
| `news.html` | none |

`scan_page` checks page text, domains, and field metadata. Process joins,
behavior flags, and new system tools are additional live-wire checks, not
return values that `scan_page` can produce by itself. Optional AI review and
a live trusted-person call require the presenter's configured agent and
remain manual demo checks.
