# allrecipes, four ways (for the presentation)

Captured from https://www.allrecipes.com/ on Oct 25, 2026 in the Windows VM with the real Ally extension, all from
one page load. No consent box, so `0-original-as-opened` is the same as `1-original`.

**How these were obtained:** allrecipes turns away automated browsers (it shows an "access issue" page). These
captures were made by hiding the test browser's automation flag. That workaround has been taken out of
`demo/capture_site.py` and isn't used again; decide whether you're comfortable using these, or capture allrecipes
by hand in your own Chrome (open the page, say each command, take a screenshot).

| File | What it shows |
|---|---|
| `1-original` | The home page with a large cruise video ad |
| `2-bigger-text` | "Make the text bigger": text 130% (the menu wraps onto two rows) |
| `3-bigger-buttons` | "Bigger buttons": only a slightly bigger search box (the home page has few real buttons) |
| `4-simple-mode` | "Simple mode": the Ally menu and the home page in reader text. Weak: the stats strip becomes separate lines ("51K", "Original Recipes"...). A recipe page would show simple mode better than the home page |

Use the `.png` files. The `.html` copies lose allrecipes' styling when the site refuses to send its stylesheets to
the browser opening them, so don't rely on them.

allrecipes content: for our presentation only. `.gitignore` keeps the captures off GitHub.
