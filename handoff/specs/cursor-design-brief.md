# Design brief: Ally scam-protection UI (read-only planning task)

You are the visual and interaction designer for "Ally", a Windows accessibility assistant for older adults (65+, many with age-related vision decline, some anxious about technology). Ally watches for risky moments (fake virus pop-ups, remote-control tools, lookalike bank sites, card forms on unfamiliar sites) and warns calmly. It is for a hackathon demo judged on design, usefulness and responsible AI. READ-ONLY: do not edit or create any files; just output the design.

Read these to ground yourself (all in the current workspace): `extension/risk.js` (the current in-page banner, Shadow DOM), `dashboard/index.html`, `dashboard/style.css`, `dashboard/app.js` (the dashboard and its tokens), and `extension/demo/risk-demo.html` (the current demo page). The warning wording is final (below); design around it, do not rewrite it.

## Hard constraints
- Dashboard CSP is `script-src 'self'; style-src 'self'`: no inline scripts, no inline event handlers, no `style=` attributes. Everything you specify must work under that (classes and external CSS only; JS may set CSS via the CSSOM).
- Banner lives in a CLOSED Shadow DOM inside arbitrary web pages: it cannot use page fonts or external resources; use system fonts only. Text is set with textContent (never innerHTML).
- No external fonts, images, CDNs or icon libraries. Inline SVG is fine.
- Calm, not alarming: no flashing, no sirens, no red-alert styling, no ALL CAPS, no exclamation marks. Respect `prefers-reduced-motion` and `prefers-color-scheme`.
- Accessibility: body text 24px or larger in the banner, 44px+ touch targets, WCAG AAA contrast (7:1) for text, visible keyboard focus, `role="alert"` on warnings, works at 200% browser zoom, screen-reader friendly, the safe/protective choice is the first button and is visually primary. Never rely on color alone.
- Ally's OWN text never contains phone numbers. Demo pages are fictional: invented brands only ("Pinecrest Savings" bank, "Larkspur PC Guard" fake security vendor), clearly labeled DEMO, served from localhost, never imitating a real brand.

## Final wording (title / body; actions in order)
1. Fake virus pop-up: "This message may not be real." / "A page says your computer is infected and asks you to call a number. Real Windows warnings never ask you to call. Your computer may be fine." [Close this page] [Tell me more]
2. Remote tool: "Someone may be able to control this computer." / "{tool} just opened. If you didn't start it, or someone on the phone asked you to, it's safest to close it." [Close it] [I know this person]
3. Lookalike address: "This address looks a little different." / "You're on {host}. Your bank is usually {usual_site}." [Go to the usual site] [Stay here]
4. Card details: "Before you enter card details." / "I don't recognise this website. New shops are fine, but a quick check first is wise." [Check with me] [Continue]
5. Remote tool plus bank page (highest priority): "Let's pause." / "Someone may be able to see this screen, and you've just opened your bank." [Close the remote tool] [It's okay]

## What to design (output a specification for each, concise and concrete)
A. The in-page warning banner (a top bar or a centered card, your call; justify it for an anxious older adult). Severity levels (medium nudge vs high "pause") must differ without alarm. Include: layout at 1280px and 390px, type scale, spacing, a color palette with exact hex values AND contrast ratios (light and dark), the button styles and states (default, hover, focus, pressed, disabled), motion (a gentle entrance, none under reduced motion), how a second smaller warning is shown when a bigger one is already on screen, and the "Ally is helping" identity (a small mark and the name) without clutter.
B. A short "Ally asks before acting" confirmation: after the user picks "Close this page" or "Close it", Ally says what it will do and asks for a clear yes (voice or click). Specify the wording shape and the visual treatment.
C. The dashboard "Privacy and permissions" screen (consent): camera on/off, pause proactive help, and a plain-language "what stays on this laptop vs what goes to the cloud" table. Real data path: camera frames stay local; screenshots for "describe screen" and voice audio go to cloud services; page text for scam checks stays local. NEVER claim "nothing leaves your computer". Specify layout, toggles that are obviously on/off (not color alone), and tone.
D. The three DEMO stage pages for the pitch: (1) fake virus pop-up page "Larkspur PC Guard" (a fictional 1-800-555-0142 may appear on THIS page only), (2) "Pinecrest Savings" real-looking bank page and its lookalike copy with a dummy card form, (3) a neutral landing page. Each carries a small unobtrusive "DEMO: not a real page" label. Give structure, copy, and CSS direction so they look believable on a projector but obviously fictional.
E. A 60-second demo run-of-show: what the judge sees and hears at each step, ordered, so the design shows its best moments (one line each).

## Output format
Plain markdown, no preamble. For each of A to E: a short rationale (2 to 3 lines), then the spec. Include ready-to-use CSS custom-property tokens (`--name: value`) and short CSS/HTML snippets where they remove ambiguity. Keep the whole answer under about 450 lines. End with a list of "Open questions for the team" (3 at most).
