# BBC News, four ways (for the presentation)

Captured from https://www.bbc.com/news on Oct 25, 2026 in the Windows VM with the real Ally extension, all from one
page load so the stories match across slides. BBC's "Terms of Use & Privacy" box was accepted first (as a reader
would) so versions 1-4 show the page itself.

| File | What it shows |
|---|---|
| `0-original-with-consent-box` | The page as first opened: the consent box covers the middle |
| `1-original` | The page after accepting (BBC's own layout, an ad and a subscription banner) |
| `2-bigger-text` | "Make the text bigger": all text 150%, layout intact |
| `3-bigger-buttons` | "Bigger buttons": menu, Subscribe, Sign In and close buttons bigger and outlined |
| `4-simple-mode` | "Simple mode": pop-up and 4 ads hidden, the Ally menu at the top, headlines in large reader text |

Each version has three files:
- `.png`: the first screen (1280x800), the safest for slides.
- `-full.png`: the whole page, top to bottom.
- `.html`: a static copy. Open it in Chrome; images load from bbc.com, so it needs the internet. BBC's scripts are
  removed so it doesn't change; Ally's styles and overlays are baked in. Ads and BBC's script-driven banners are
  missing from the HTML copies (they're in the screenshots).

BBC content: these files are for our presentation only. `.gitignore` keeps them off GitHub; don't publish them.

To capture again (in the VM, from `C:\ally`):
`python demo\capture_site.py https://www.bbc.com/news C:\ally\demo\bbc-news` (the first file is then named
`0-original-as-opened`).
