# Wikipedia, four ways (for the presentation)

Captured from https://en.wikipedia.org/wiki/Main_Page on Oct 25, 2026 in the Windows VM with the real Ally
extension, all from one page load. Wikipedia shows no consent box, so `0-original-as-opened` is the same as
`1-original`.

| File | What it shows |
|---|---|
| `1-original` | The Main Page (an event banner, featured article, "help us improve" box, Appearance panel) |
| `2-bigger-text` | "Make the text bigger": all text 130%, layout intact |
| `3-bigger-buttons` | "Bigger buttons": Search and the Appearance "hide" button bigger and outlined |
| `4-simple-mode` | "Simple mode": the Ally menu at the top and the page in large reader text with the featured photo (a stray comma sits on its own line under the title) |

Each version: `.png` (first screen, 1280x800), `-full.png` (whole page) and `.html` (a static copy; open in Chrome,
images and styles load from wikipedia.org, so it needs the internet). Wikipedia text is CC BY-SA; `.gitignore`
keeps the captures off GitHub anyway.

To capture again (in the VM, from `C:\ally`):
`python demo\capture_site.py https://en.wikipedia.org/wiki/Main_Page C:\ally\demo\wikipedia`
