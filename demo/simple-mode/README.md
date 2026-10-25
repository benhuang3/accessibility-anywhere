# Simplified mode demo: a cluttered news site

`news.html` is a fictional cable-news style page ("Ledger News", maroon instead of the usual red) built to be as
overwhelming as possible:
- other news sites' headlines with thumbnails along the very top, and a "we've updated our terms" notice;
- a maroon bar with a complicated multi-level menu, Watch / Listen / Live TV, and a ringing notification bell;
- a scrolling BREAKING banner and a sliding car ad;
- a wall of image headlines, a commission disclaimer, "Paid partner" boxes with "Ad Feedback" links and fine print;
- notifications everywhere: a "wants to show notifications" window, an open notifications panel (9 new), an
  "open the app" bar, alert toasts and a "2 free articles left" toast;
- floating windows: a live TV picture-in-picture window and a tilted "Trending on partner sites" window;
- a newsletter pop-up, a chat bubble, a share bar and a cookie bar ("we and our 847 partners").

Somewhere in the middle, in tiny pale grey type, is the actual story: **"Heartwarming: Dog and Cat are best
friends"**, with a faint little "Show image" button.

After **"Ally, simple mode"**, all of that is gone: the story is in large dark text, the button is big and
yellow, and pressing it shows the photo of the dog and the cat (`dog-and-cat.jpg`, a copy of the team's
`demo/dogcat.jpg`; check its license before publishing a video). The headline photos are public-domain and CC0
pictures from Wikimedia Commons (`PHOTO_CREDITS.txt`), picked by topic, so some are only loosely related. The site's sections are in the big **Ally menu**
button at the top.

## Run it

From this folder:

```powershell
python -m http.server 8001
```

Open **http://localhost:8001/news.html** in Chrome with the Ally extension connected to the running agent.

- Use `localhost`, not `127.0.0.1`: Ally's scam rules flag raw IP addresses (`untrusted_site`). On `localhost`
  the page scores no risks.
- Reload the Ally extension (chrome://extensions) after pulling, so it has the demo-tag support below.
- For a rehearsal-proof run, set `[page_enlarge] ai_check = false` in `config.toml`: the Claude look-over that
  runs after Simplified mode is good on real sites, but the demo doesn't need it.

## The beat (about 20 seconds)

1. Show the page: "This is what a lot of news sites look like. Can you even find the story?"
2. Say **"Ally, simple mode"** (or "this site is too busy").
3. Ally says "One moment, I'm making this page simpler", shows a "Simplifying this page…" card over the cluttered
   page for 5 seconds (the page asks for that pause with `data-delay-ms="5000"` on its demo meta tag, so the
   audience can take in the mess), then everything clears at once (about 5.3 s in all): *"Simple mode is on. I hid a cookie banner, 17 pop-ups and banners, a chat
   button, 5 ads and a share bar and stopped moving animations, put a big Ally menu button at the top, made the
   text bigger (200%) and made buttons and boxes bigger."*
4. Press the big **Show image** button (or say "click show image"): Biscuit and Pepper appear. "Zoom in on the
   picture" opens it in Ally's picture viewer.
5. Undo: the **Undo** chip, or **"simple mode off"**, puts the cluttered page back.

## How the demo stays reliable: `data-demo-clutter`

Every distracting piece carries `data-demo-clutter="<kind>"` (`banner`, `animation`, `popup`, `toast`, `ad`,
`cookie`, `chat`, `share`, `promo`, `menu`). The page opts in with `<meta name="ally-demo" content="simple-mode">`,
and on pages with that tag Ally's clutter step hides everything tagged, on top of what its normal rules find, so
the demo never depends on a heuristic. A tagged `menu` is hidden too, and the menus step still reads its links for
the Ally menu. Real websites don't have the meta tag, so the tag does nothing there; Ally's safety checks (never
hide the main content, a password or card field, or Ganesh's risk banner) still apply to tagged pieces.

The story is marked up with plain `<div>`s, like many news sites, so Ally treats the page as a general page
(clean it up, bigger text and buttons) rather than opening the reader view, which leaves buttons out. The
big yellow button is the page's own styling for when Ally has enlarged it (Ally marks `<html>` with
`data-ally-text-scale`); everything else is Ally's normal Simplified mode.

Blinking is kept at or under one flash per second (the photosensitivity limit is three). Every animation only
moves, scales or fades: in the demo VM (no graphics card) animations that repaint the page made every frame so
slow that Simplified mode took 10-14 s; with transform/opacity animations the work itself takes about 0.3 s
(plus the 5 s pause the page asks for; 5 s is the most a page can ask for).
Nothing on the page is real except the photo: the network, people, pets, businesses, ads and other news sites are made up, and nothing is sent.

Tested in the Windows VM with the real extension: general page, all 26 tagged pieces hidden, text 200%, the
button grows from 64x14 to 279x87 px, the photo shows, the Ally menu holds the site's sections (12 entries), no
risk warnings on localhost.
