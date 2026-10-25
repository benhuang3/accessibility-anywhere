# UI/UX: Clarity overlay + talking mascot

Branch: `uiux`. Scope: the look of Ally's help surfaces. These files are **Ganesh's** (`ally/overlay/`,
`dashboard/`, `extension/`) — this branch is a proposal to review with him, not to merge unseen.

Direction chosen by Ben: **Clarity** for the overlay (near-black on white, electric-blue accent, thick
borders, amber reserved for risk), plus a placeholder **mascot** for the agent that "talks" when it speaks.
Concept map and other themes: the shared concept artifact (Liquid Glass + four alt themes).

## Clarity token set

The single source for both surfaces. The overlay is tkinter, so it reads these as Python color constants;
the web surfaces use the same values as CSS custom properties.

| Role | Light | Note |
|---|---|---|
| Background | `#FFFFFF` | page / card |
| Ink | `#0A0C10` | body + titles, near-black not pure black |
| Ink soft | `#2B2B2B` | secondary body |
| Line / border | `#0A0C10` | thick (2–3px) — the Clarity signature |
| Accent | `#0B57D0` | primary buttons, focus, "look here" highlight |
| Accent ink | `#FFFFFF` | text on accent |
| Attention / risk | `#9A4B00` | risk border + eyebrow; wash `#FFF4DF` |
| Good | `#0B6B2B` | confirmations |
| Focus ring | `#FFBF00` | 3px, offset 2px |

A dark variant (for the always-on overlay at night) is a follow-up: swap bg `#0B0E16`, ink `#F1F4FA`,
accent `#7FA3FF`, keeping the same contrast ratios.

## Done on this branch

- **Overlay recolored to Clarity** — `ally/overlay/layout.py` (INFO/RISK/HIGHLIGHT constants) and
  `ally/overlay/overlay.py` (`PRIMARY_BG` → accent blue, risk eyebrow uses the amber constant, border padding
  2 → 3px). Logic untouched; only colors and the border width changed.
- **Talking mascot on the dashboard** — placeholder inline SVG character in the Ask card
  (`dashboard/index.html`), beside a speech bubble that holds the agent's reply. `dashboard/style.css` adds
  the mascot, bubble, and a mouth/blink animation gated behind `.talking`. `dashboard/app.js` drives
  `.talking` from the live `speech.speaking` status and pulses it when a reply arrives. CSP-safe: inline SVG
  markup only, animation in the stylesheet, state toggled by class — no inline styles or scripts. Honours the
  existing `prefers-reduced-motion` rule.

## Follow-ups (not built yet)

1. **Mascot on the overlay** — tkinter needs a raster (`PhotoImage`, PNG/GIF). Rasterize the SVG to
   `ally/overlay/mascot.png`, load once and keep a reference (Tk GCs unreferenced images), draw it in the info
   card header. Keep it off risk cards so it never softens a warning.
2. **Mascot in the extension popup** (`extension/popup.html`) — same inline SVG, same `.talking` hook off the
   reply. Trivial once the dashboard version is signed off.
3. **Mascot expressions** — a worried face for risk cards, a happy one on success. Driven by event `kind`.
4. **Clarity on the dashboard + popup** — the dashboard still uses its own blue (`#1b3fa0`); align it to the
   accent above if we want one palette everywhere.
5. **Final art** — replace the placeholder SVG. The `#mouth`, `#eyeL`, `#eyeR` ids are the animation contract.

## Review notes for Ganesh

- Nothing in `ally/risk/` or the risk wording was touched.
- The overlay change is colors + a 1px border bump; the panel/offer logic and tests are unchanged.
- Dashboard changes are additive (new `.agent` block and CSS); the `#reply` element and its `aria-live` and
  status/colour classes are preserved, so `app.js`'s existing `showReply` path still works.
