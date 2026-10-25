// Reader view: the page's main content (headings, paragraphs, lists, quotes, links, pictures with alt text)
// in large, high-contrast type, drawn in a shadow-DOM overlay over the page. See docs/page_steps.md.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.reader) return;

  const HOST_ID = "ally-reader-host";
  const STRIP_PX = 64;
  const SKIP_TAGS = new Set([
    "NAV", "ASIDE", "FOOTER", "FORM", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SCRIPT", "STYLE", "NOSCRIPT",
    "TEMPLATE", "SVG", "IFRAME", "CANVAS", "VIDEO", "AUDIO", "OBJECT", "EMBED", "DIALOG", "MENU", "LABEL",
  ]);
  const SKIP_ROLES = /^(navigation|complementary|banner|contentinfo|search|dialog|alertdialog|menu|menubar|toolbar|form)$/;
  const JUNK = /(^|[\s_-])(ads?|advert\w*|sponsor\w*|promo\w*|share|sharing|social|newsletter|related|recommended|cookie\w*|comments?|breadcrumbs?|subscribe|paywall)([\s_-]|$)/i;
  const BLOCK = new Set([
    "P", "DIV", "SECTION", "ARTICLE", "MAIN", "HEADER", "H1", "H2", "H3", "H4", "H5", "H6", "UL", "OL", "LI", "DL",
    "BLOCKQUOTE", "PRE", "TABLE", "FIGURE", "FIGCAPTION", "HR", "ADDRESS", "DETAILS",
  ]);
  const MIN_SOURCE_CHARS = 500;

  const CSS = `
    .wrap { position: fixed; inset: 0; overflow: auto; background: #111; color: #f5f5f5;
            font: var(--ally-font, 26px)/1.6 Verdana, "Atkinson Hyperlegible", Arial, sans-serif; }
    .wrap:focus { outline: none; }
    .wrap.strip { top: ${STRIP_PX}px; }
    .bar { position: sticky; top: 0; display: flex; justify-content: flex-end; padding: 12px 16px;
           background: inherit; z-index: 1; }
    .close { font: 700 22px/1.2 Arial, sans-serif; min-height: 56px; min-width: 140px; padding: 12px 22px;
             background: #ffeb3b; color: #000; border: 3px solid #000; border-radius: 10px; cursor: pointer; }
    .close:focus-visible, a:focus-visible { outline: 4px solid #4fc3f7; outline-offset: 3px; }
    article { max-width: 70ch; margin: 0 auto; padding: 0 1.2em 6em; }
    h1, h2, h3 { line-height: 1.3; margin: 1.4em 0 0.6em; color: #fff; }
    h1 { font-size: 1.5em; margin-top: 0.4em; }
    h2 { font-size: 1.25em; }
    h3 { font-size: 1.1em; }
    p, li, blockquote, pre { letter-spacing: 0.02em; word-spacing: 0.08em; }
    p { margin: 0 0 1em; }
    ul, ol { margin: 0 0 1em; padding-left: 1.6em; }
    li { margin: 0 0 0.5em; }
    blockquote { margin: 0 0 1em; padding: 0.2em 0 0.2em 1em; border-left: 6px solid #888; }
    pre { white-space: pre-wrap; font-size: 0.85em; background: #222; padding: 0.6em; border-radius: 6px; }
    code { font-size: 0.9em; }
    a { color: #8ecbff; text-decoration: underline; text-underline-offset: 0.15em; }
    a:visited { color: #d1a8ff; }
    img { display: block; max-width: 100%; max-height: 60vh; height: auto; margin: 0.5em auto; }
    figure { margin: 0 0 1em; }
    .caption { font-size: 0.8em; opacity: 0.9; text-align: center; }
    @media (prefers-color-scheme: light) {
      .wrap { background: #fffbea; color: #111; }
      h1, h2, h3 { color: #000; }
      a { color: #0645ad; }
      a:visited { color: #5a2d91; }
      pre { background: #f0ead0; }
      blockquote { border-left-color: #555; }
    }`;

  let current = null;

  // ------------------------------------------------------------ finding the main content
  function skip(el) {
    if (SKIP_TAGS.has(el.tagName) || P.isAlly(el)) return true;
    if (el.hasAttribute("data-ally-clutter") || el.hidden || el.getAttribute("aria-hidden") === "true") return true;
    if (SKIP_ROLES.test(el.getAttribute("role") || "")) return true;
    if (el.tagName === "HEADER" && !el.querySelector("h1, h2")) return true;
    const hint = `${typeof el.className === "string" ? el.className : ""} ${el.id || ""}`;
    if (JUNK.test(hint) && !/article|content|main|body|story/i.test(hint)) return true;
    // Breaks and not-yet-loaded pictures have no size; only skip them when CSS hides them.
    if (el.tagName === "BR" || el.tagName === "IMG") return !el.checkVisibility({ checkVisibilityCSS: true });
    return !P.visible(el);
  }

  function textLen(el) {
    let n = 0;
    for (const p of el.querySelectorAll("p, li, pre, blockquote")) n += p.textContent.trim().length;
    return n;
  }

  // Readability-style: score each paragraph's parent and grandparent, take the best.
  function scoredSource() {
    const scores = new Map();
    for (const p of document.querySelectorAll("p, pre, td")) {
      if (P.isAlly(p) || p.closest("[data-ally-clutter], nav, aside, footer, form")) continue;
      const len = p.textContent.trim().length;
      if (len < 25) continue;
      const s = 1 + p.textContent.split(",").length + Math.min(Math.floor(len / 100), 3);
      const parent = p.parentElement;
      if (parent) scores.set(parent, (scores.get(parent) || 0) + s);
      if (parent && parent.parentElement) scores.set(parent.parentElement, (scores.get(parent.parentElement) || 0) + s / 2);
    }
    let best = null;
    let bestScore = 0;
    for (const [el, s] of scores) {
      if (s > bestScore && el !== document.documentElement) {
        best = el;
        bestScore = s;
      }
    }
    return best;
  }

  function findSource() {
    let best = null;
    let bestLen = 0;
    for (const el of document.querySelectorAll("article, main, [role=main]")) {
      if (P.isAlly(el) || P.allyHidden(el) || !P.visible(el)) continue;
      const n = textLen(el);
      if (n > bestLen) {
        best = el;
        bestLen = n;
      }
    }
    if (best && bestLen >= MIN_SOURCE_CHARS) return { el: best, how: best.tagName.toLowerCase() === "article" ? "article" : "main" };
    const scored = scoredSource();
    if (scored && textLen(scored) >= MIN_SOURCE_CHARS / 2) return { el: scored, how: "scored" };
    if (best) return { el: best, how: "main" };
    return { el: document.body, how: "body" };
  }

  // ------------------------------------------------------------ copying it into clean elements
  function safeHref(a) {
    const href = a.href;
    return /^(https?:|mailto:|tel:)/i.test(href) ? href : "";
  }

  function picture(img, counts) {
    const alt = (img.getAttribute("alt") || "").trim();
    const src = img.currentSrc || img.src;
    if (!alt || !/^(https?:|data:image\/)/i.test(src)) return null;
    const out = document.createElement("img");
    out.src = src;
    out.alt = alt;
    out.loading = "lazy";
    out.referrerPolicy = "no-referrer";
    counts.images++;
    return out;
  }

  // Copies inline content (text, links, emphasis, code, line breaks, pictures) into target.
  function copyInline(node, target, ctx) {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        target.append(child.nodeValue.replace(/\s+/g, " "));
        continue;
      }
      if (child.nodeType !== 1 || skip(child)) continue;
      const tag = child.tagName;
      if (tag === "BR") target.append(document.createElement("br"));
      else if (tag === "IMG") {
        const img = ctx.images ? picture(child, ctx.counts) : null;
        if (img) target.append(img);
      } else if (tag === "A") {
        const href = safeHref(child);
        if (!href) {
          copyInline(child, target, ctx);
          continue;
        }
        const a = document.createElement("a");
        a.href = href;
        copyInline(child, a, ctx);
        if (!a.textContent.trim() && !a.querySelector("img")) continue;
        ctx.counts.links++;
        target.append(a);
      } else if (/^(STRONG|B|EM|I|CODE|MARK|SUB|SUP)$/.test(tag)) {
        const el = document.createElement({ B: "strong", I: "em" }[tag] || tag.toLowerCase());
        copyInline(child, el, ctx);
        target.append(el);
      } else copyInline(child, target, ctx);
    }
  }

  function hasBlockChild(el) {
    for (const c of el.children) if (BLOCK.has(c.tagName)) return true;
    return false;
  }

  function tidy(el) {
    // Collapse whitespace at the edges so empty-looking blocks can be dropped.
    const text = el.textContent.replace(/\s+/g, " ").trim();
    return text.length > 0 || !!el.querySelector("img");
  }

  function addParagraph(target, fill) {
    const p = document.createElement("p");
    fill(p);
    if (tidy(p)) target.append(p);
    return p;
  }

  function copyList(list, ctx) {
    const out = document.createElement(list.tagName === "OL" ? "ol" : "ul");
    if (list.tagName === "OL" && list.start > 1) out.start = list.start;
    for (const li of list.children) {
      if (li.tagName !== "LI" || skip(li)) continue;
      const item = document.createElement("li");
      copyBlocks(li, item, ctx, true);
      if (tidy(item)) out.append(item);
    }
    if (!out.children.length) return null;
    ctx.counts.lists++;
    return out;
  }

  function copyTable(table, target, ctx) {
    for (const row of table.querySelectorAll("tr")) {
      const cells = Array.from(row.children).filter((c) => /^T[DH]$/.test(c.tagName));
      addParagraph(target, (p) => {
        cells.forEach((c, i) => {
          if (i) p.append(" | ");
          copyInline(c, p, ctx);
        });
      });
    }
  }

  // Copies block content from node into target. In list items (inline=true) loose text stays inline.
  function copyBlocks(node, target, ctx, inline = false) {
    let run = null;
    const flush = () => {
      if (run && tidy(run)) {
        if (inline) target.append(...run.childNodes);
        else {
          target.append(run);
          ctx.counts.paragraphs++;
        }
      }
      run = null;
    };
    for (const child of Array.from(node.childNodes)) {
      const isEl = child.nodeType === 1;
      if (isEl && skip(child)) continue;
      if (!isEl || (!BLOCK.has(child.tagName) && !(child.tagName !== "IMG" && hasBlockChild(child)))) {
        if (child.nodeType !== 1 && child.nodeType !== 3) continue;
        if (!run) run = document.createElement("p");
        copyInline({ childNodes: [child] }, run, ctx);
        continue;
      }
      flush();
      copyBlock(child, target, ctx);
    }
    flush();
  }

  function copyBlock(el, target, ctx) {
    const tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) {
      const level = Math.min(3, Number(tag[1]));
      const h = document.createElement(`h${level}`);
      copyInline(el, h, ctx);
      if (tidy(h)) {
        target.append(h);
        ctx.counts.headings++;
      }
    } else if (tag === "P" || tag === "ADDRESS") {
      if (hasBlockChild(el)) copyBlocks(el, target, ctx);
      else if (tidy(addParagraph(target, (p) => copyInline(el, p, ctx)))) ctx.counts.paragraphs++;
    } else if (tag === "UL" || tag === "OL") {
      const list = copyList(el, ctx);
      if (list) target.append(list);
    } else if (tag === "BLOCKQUOTE") {
      const q = document.createElement("blockquote");
      copyBlocks(el, q, ctx);
      if (tidy(q)) {
        target.append(q);
        ctx.counts.quotes++;
      }
    } else if (tag === "PRE") {
      const pre = document.createElement("pre");
      pre.textContent = el.textContent;
      if (tidy(pre)) target.append(pre);
    } else if (tag === "TABLE") {
      copyTable(el, target, ctx);
    } else if (tag === "FIGURE") {
      const fig = document.createElement("figure");
      for (const img of el.querySelectorAll("img")) {
        const out = ctx.images ? picture(img, ctx.counts) : null;
        if (out) fig.append(out);
      }
      const cap = el.querySelector("figcaption");
      if (cap && fig.children.length) {
        const p = document.createElement("p");
        p.className = "caption";
        copyInline(cap, p, ctx);
        if (tidy(p)) fig.append(p);
      }
      if (fig.children.length) target.append(fig);
    } else if (tag === "HR" || tag === "FIGCAPTION") {
      // dropped: rules are decoration; captions only make sense next to their picture
    } else {
      copyBlocks(el, target, ctx);
    }
  }

  function build(source, opts) {
    const article = document.createElement("article");
    const counts = { headings: 0, paragraphs: 0, lists: 0, quotes: 0, links: 0, images: 0 };
    const ctx = { counts, images: opts.images !== false };
    copyBlock(source, article, ctx);
    if (!article.querySelector("h1")) {
      const h1 = document.createElement("h1");
      const og = document.querySelector("meta[property='og:title']");
      h1.textContent = (og && og.content) || document.title || "Reader view";
      article.prepend(h1);
      counts.headings++;
    }
    // Nothing useful came out (an odd page layout): fall back to the visible text.
    if (article.textContent.trim().length < 80) {
      for (const line of (source.innerText || "").split(/\n+/)) {
        const t = line.trim();
        if (t.length < 2) continue;
        const p = document.createElement("p");
        p.textContent = t;
        article.append(p);
        counts.paragraphs++;
      }
    }
    return { article, counts };
  }

  // ------------------------------------------------------------ overlay
  function sheet() {
    const s = new CSSStyleSheet();
    s.replaceSync(CSS);
    return s;
  }

  function styleHost(host, withStrip) {
    const set = (k, v) => host.style.setProperty(k, v, "important");
    set("all", "initial");
    set("position", "fixed");
    set("inset", withStrip ? `${STRIP_PX}px 0 0 0` : "0");
    set("z-index", "2147483646");
    set("display", "block");
  }

  function open(opts = {}) {
    close();
    if (P.modules.picture) P.modules.picture.close(); // one overlay at a time: the viewer would trap the keys
    if (typeof globalThis.__allyReaderClose === "function") globalThis.__allyReaderClose(); // the older reader
    const stale = document.getElementById(HOST_ID);
    if (stale) stale.remove();

    const fontPx = Math.max(12, Math.min(72, Number(opts.fontPx) || 26));
    const withStrip = !!opts.withStrip;
    const source = findSource();
    const { article, counts } = build(source.el, opts);

    const host = document.createElement("div");
    host.id = HOST_ID;
    styleHost(host, withStrip);
    const root = host.attachShadow({ mode: "open" });
    try {
      root.adoptedStyleSheets = [sheet()];
    } catch {
      const style = document.createElement("style");
      style.textContent = CSS;
      root.append(style);
    }

    const wrap = document.createElement("div");
    wrap.className = withStrip ? "wrap strip" : "wrap";
    wrap.style.setProperty("--ally-font", `${fontPx}px`);
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", "Reader view");
    wrap.tabIndex = -1;
    const bar = document.createElement("div");
    bar.className = "bar";
    const button = document.createElement("button");
    button.className = "close";
    button.type = "button";
    button.textContent = "Close (Esc)";
    bar.append(button);
    wrap.append(bar, article);
    root.append(wrap);

    const unlock = P.lockScroll();
    const prevFocus = document.activeElement;
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      close();
    };
    // Same-page links (#section) would scroll the page hidden under the reader: close first, then follow.
    const onClick = (e) => {
      const a = e.target.closest && e.target.closest("a[href]");
      if (!a) return;
      const url = new URL(a.href);
      if (url.origin + url.pathname + url.search === location.origin + location.pathname + location.search && url.hash) close();
    };
    button.addEventListener("click", () => close());
    root.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey, true);
    document.documentElement.appendChild(host);
    wrap.focus({ preventScroll: true });

    current = { host, unlock, onKey, prevFocus };
    globalThis.__allyReaderClose = close;
    return { said: "opened reader view", source: source.how, fontPx, withStrip, ...counts };
  }

  function close() {
    if (!current) {
      const stale = document.getElementById(HOST_ID);
      if (!stale) return { said: "" };
      stale.remove(); // a reader drawn by older code
      return { said: "closed reader view" };
    }
    const { host, unlock, onKey, prevFocus } = current;
    current = null;
    document.removeEventListener("keydown", onKey, true);
    host.remove();
    unlock();
    if (globalThis.__allyReaderClose === close) globalThis.__allyReaderClose = null;
    if (prevFocus && prevFocus.isConnected && typeof prevFocus.focus === "function") prevFocus.focus({ preventScroll: true });
    return { said: "closed reader view" };
  }

  P.registerOff("reader", close);
  P.modules.reader = { open, close };
})();
