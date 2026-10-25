// Picture viewer: lists the described pictures on screen and shows one large in a shadow-DOM overlay where
// it can be zoomed and panned. The page DOM is only read: no marks, no CSS layers. See docs/picture_viewer.md.

(() => {
  const P = globalThis.AllyPage;
  if (!P || P.modules.picture) return;

  const HOST_ID = "ally-picture-host";
  const READER_ID = "ally-reader-host";
  const GONE = "That picture isn't on the page any more.";
  const MAX_ITEMS = 60;
  const SCALES = [1, 1.5, 2, 3, 4, 6, 8];
  const POINTS = [[0.5, 0.5], [0.2, 0.2], [0.8, 0.2], [0.2, 0.8], [0.8, 0.8]];
  const SKIP_TAGS = new Set(["CANVAS", "VIDEO", "IFRAME", "OBJECT", "EMBED", "AUDIO"]);
  const SUPPORTED = /^image\/(jpeg|jpg|pjpeg|png|apng|gif|webp|avif|svg\+xml|bmp|x-icon|vnd\.microsoft\.icon)$/i;
  const SVG_SHAPES = "path, circle, rect, ellipse, line, polyline, polygon, text, tspan, use";
  const MAX_SHAPES = 1500;

  // A runtime injected before VERSION 2 doesn't know this host; reset still has to remove it.
  if (!P.HOSTS.includes(HOST_ID)) P.HOSTS.push(HOST_ID);

  const CSS = `
    .wrap { position: fixed; inset: 0; overflow: hidden; background: rgba(0, 0, 0, 0.92); color: #fff;
            font: 22px/1.3 Verdana, Arial, sans-serif; touch-action: none; user-select: none; }
    .wrap:focus { outline: none; }
    .stage { position: absolute; inset: 0; cursor: default; }
    .stage.zoomed { cursor: grab; }
    .stage.dragging { cursor: grabbing; }
    .pic { position: absolute; left: 0; top: 0; max-width: none; max-height: none; margin: 0;
           -webkit-user-drag: none; user-select: none; }
    .pic.anim { transition: width 0.15s ease-out, height 0.15s ease-out, transform 0.15s ease-out; }
    .bg { background-position: center; background-size: contain; background-repeat: no-repeat; }
    .bar { position: absolute; left: 0; right: 0; top: 0; display: flex; flex-wrap: wrap; justify-content: center;
           gap: 12px; padding: 12px; background: rgba(0, 0, 0, 0.7); }
    button { font: 700 22px/1.2 Arial, sans-serif; min-height: 56px; min-width: 56px; padding: 10px 20px;
             background: #fff; color: #000; border: 3px solid #000; border-radius: 10px; cursor: pointer; }
    button.close { background: #ffeb3b; }
    button[aria-disabled="true"] { opacity: 0.5; cursor: default; }
    button[hidden] { display: none; }
    button:focus-visible { outline: 4px solid #4fc3f7; outline-offset: 3px; }
    .caption { position: absolute; left: 0; right: 0; bottom: 0; margin: 0; padding: 12px 24px;
               background: rgba(0, 0, 0, 0.85); color: #fff; font: 26px/1.35 Verdana, Arial, sans-serif;
               text-align: center; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3;
               overflow: hidden; }
    .caption[hidden] { display: none; }`;

  let ids = new Map(); // "p1" -> WeakRef(element), rebuilt by every list()
  let generation = 0; // which list() the ids came from
  const BOXES_ID = "ally-picture-boxes";
  if (!P.HOSTS.includes(BOXES_ID)) P.HOSTS.push(BOXES_ID);
  const claudeSaid = new Map(); // picture source -> description from Claude, for this page

  // Which picture this is, across list() calls: its file (or, for inline SVG, its place in the page).
  function sourceKey(el) {
    if (el.tagName === "IMG") return el.currentSrc || el.src || P.key(el);
    const r = el.getBoundingClientRect();
    const size = `${Math.round(r.width)}x${Math.round(r.height)}`; // a position path alone can repeat in card grids
    if (isSvg(el)) return `svg:${P.key(el)}:${size}:${(el.textContent || "").trim().slice(0, 60)}`;
    return bgUrl(el) || `${P.key(el)}:${size}`;
  }

  // The page's own descriptions, then Claude's for this picture, if any.
  function descriptionsOf(el) {
    const out = describe(el);
    const said = claudeSaid.get(sourceKey(el));
    if (said && !out.some((d) => d.text.toLowerCase() === said.toLowerCase())) out.push({ text: said, kind: "claude" });
    return out;
  }
  let current = null;

  // ------------------------------------------------------------ finding pictures
  function clean(text) {
    return String(text || "").replace(/\s+/g, " ").trim().slice(0, 300);
  }

  function refText(el, attr) {
    const root = el.getRootNode();
    const find = (id) => (typeof root.getElementById === "function" ? root.getElementById(id) : document.getElementById(id));
    return (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean)
      .map((id) => find(id))
      .filter(Boolean)
      .map((n) => n.textContent)
      .join(" ");
  }

  function isSvg(el) {
    return el instanceof SVGSVGElement;
  }

  function svgTitle(el) {
    const t = el.querySelector(":scope > title");
    return t ? t.textContent : "";
  }

  function bgUrl(el) {
    const m = /url\(\s*(['"]?)(.*?)\1\s*\)/.exec(getComputedStyle(el).backgroundImage || "");
    return m ? abs(m[2]) : "";
  }

  function abs(url) {
    try {
      return new URL(url, document.baseURI).href;
    } catch {
      return "";
    }
  }

  function describe(el) {
    const out = [];
    const seen = new Set();
    const add = (text, kind) => {
      const t = clean(text);
      if (!t || seen.has(t.toLowerCase())) return;
      seen.add(t.toLowerCase());
      out.push({ text: t, kind });
    };
    if (isSvg(el)) {
      add(svgTitle(el), "title");
      add(el.getAttribute("aria-label"), "label");
      return out;
    }
    if (el.tagName === "IMG") add(el.getAttribute("alt"), "alt");
    add(refText(el, "aria-labelledby"), "labelledby");
    add(el.getAttribute("aria-label"), "label");
    const fig = el.closest("figure");
    const cap = fig && (fig.querySelector(":scope > figcaption") || fig.querySelector("figcaption"));
    if (cap) add(cap.textContent, "caption");
    else add(nearbyCaption(el), "caption");
    add(refText(el, "aria-describedby"), "describedby");
    add(el.getAttribute("title"), "title");
    const link = el.parentElement && el.parentElement.closest("a");
    if (link) {
      add(link.getAttribute("aria-label"), "link");
      add(link.getAttribute("title"), "link");
    }
    return out;
  }

  // A caption next to the picture without a <figure> (Wikipedia's infobox, many news sites): an element with
  // "caption" in its class, close by, in a box that holds only this one picture.
  function nearbyCaption(el) {
    let box = el.parentElement;
    for (let i = 0; box && i < 4; i++, box = box.parentElement) {
      if (box.querySelectorAll("img, svg, picture").length > (el.closest("picture") ? 2 : 1)) return "";
      for (const c of box.querySelectorAll('[class*="caption" i]')) {
        if (c.contains(el) || c.querySelector("img, svg")) continue;
        const text = (c.textContent || "").trim();
        if (text) return text;
      }
    }
    return "";
  }

  function kindOf(el) {
    if (SKIP_TAGS.has(el.tagName)) return "";
    if (el.tagName === "IMG") return "img";
    if (isSvg(el)) {
      if (el.parentElement && el.parentElement.closest("svg")) return ""; // nested: the outer one counts
      return clean(svgTitle(el)) || clean(el.getAttribute("aria-label")) ? "svg" : "";
    }
    if (el instanceof SVGElement) return "";
    if (el.getAttribute("role") === "img" && clean(el.getAttribute("aria-label")) && bgUrl(el)) return "bg";
    return "";
  }

  function decorative(el) {
    const role = (el.getAttribute("role") || "").trim().toLowerCase();
    if (role === "presentation" || role === "none") return true;
    if (el.closest('[aria-hidden="true"]')) return true;
    return el.tagName === "IMG" && el.hasAttribute("alt") && !el.getAttribute("alt").trim();
  }

  function candidates() {
    const out = [];
    const seen = new Set();
    for (const el of document.querySelectorAll("img, svg, [role=img]")) {
      if (seen.has(el) || P.isAlly(el)) continue;
      seen.add(el);
      out.push([el, document]);
    }
    // Ally's reader view shows the article's pictures in its shadow root; those count too.
    const reader = document.getElementById(READER_ID);
    const root = reader && reader.shadowRoot;
    if (root) for (const el of root.querySelectorAll("img")) out.push([el, root]);
    return out;
  }

  // Whether el is what the user sees at (x, y). Elements above it that only catch clicks (a "stretched link"
  // over a news card) or are see-through (a gradient over a hero picture) don't hide it.
  function hits(el, root, x, y) {
    for (const hit of root.elementsFromPoint(x, y)) {
      if (hit === el || el.contains(hit)) return true;
      if (hit.contains(el)) continue; // a parent, e.g. the link around the picture
      if (!seeThrough(hit)) return false;
    }
    return false;
  }

  function seeThrough(node) {
    const cs = getComputedStyle(node);
    if (/url\(/.test(cs.backgroundImage)) return false;
    const c = /rgba?\(([^)]+)\)/.exec(cs.backgroundColor);
    const alpha = c ? (c[1].split(",")[3] === undefined ? 1 : parseFloat(c[1].split(",")[3])) : 0;
    return alpha < 0.5 && parseFloat(cs.opacity) > 0 && !/^(IMG|VIDEO|CANVAS|IFRAME|svg)$/i.test(node.tagName);
  }

  // The part of el that the user can see now, in viewport CSS px, or null.
  function seenPart(el, root, r, vw, vh) {
    const x1 = Math.max(0, r.left);
    const y1 = Math.max(0, r.top);
    const x2 = Math.min(vw, r.right);
    const y2 = Math.min(vh, r.bottom);
    if (x2 <= x1 || y2 <= y1) return null;
    const w = x2 - x1;
    const h = y2 - y1;
    const frac = (w * h) / (r.width * r.height);
    if (frac < 0.4 && !(w >= 200 && h >= 200)) return null;
    let open = 0;
    for (const [fx, fy] of POINTS) if (hits(el, root, x1 + w * fx, y1 + h * fy)) open++;
    if (open < 2) return null;
    return { x: x1, y: y1, w, h, frac };
  }

  function naturalOf(el, kind, r) {
    if (kind === "img") return { w: el.naturalWidth || 0, h: el.naturalHeight || 0 };
    if (kind === "svg") {
      const vb = el.viewBox && el.viewBox.baseVal;
      if (vb && vb.width > 0 && vb.height > 0) return { w: Math.round(vb.width), h: Math.round(vb.height) };
      return { w: Math.round(r.width), h: Math.round(r.height) };
    }
    return { w: 0, h: 0 };
  }

  // Top to bottom, then left to right; pictures whose tops are close count as one row.
  function readingOrder(items) {
    items.sort((a, b) => a.y - b.y || a.x - b.x);
    const out = [];
    let row = [];
    for (const it of items) {
      if (row.length && it.y - row[0].y > 24) {
        out.push(...row.sort((a, b) => a.x - b.x));
        row = [];
      }
      row.push(it);
    }
    out.push(...row.sort((a, b) => a.x - b.x));
    return out;
  }

  // The open viewer would cover every picture; hide it while measuring (synchronous, so it never flickers).
  function list(opts = {}) {
    if (!current) return listNow(opts);
    const style = current.host.style;
    style.setProperty("display", "none", "important");
    try {
      return listNow(opts);
    } finally {
      style.setProperty("display", "block", "important");
    }
  }

  function listNow({ minPx = 80 } = {}) {
    const min = Number(minPx) || 80;
    const html = document.documentElement;
    // min(): in quirks mode <html>'s client size is the whole document
    const vw = Math.min(html.clientWidth || innerWidth, innerWidth);
    const vh = Math.min(html.clientHeight || innerHeight, innerHeight);
    const found = [];
    for (const [el, root] of candidates()) {
      const kind = kindOf(el);
      if (!kind || decorative(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width < min || r.height < min) continue;
      if (r.right <= 0 || r.bottom <= 0 || r.left >= vw || r.top >= vh) continue;
      if (!P.visible(el)) continue;
      const part = seenPart(el, root, r, vw, vh);
      if (!part) continue;
      const descriptions = descriptionsOf(el);
      found.push({
        el,
        kind,
        x: Math.round(part.x),
        y: Math.round(part.y),
        w: Math.round(part.w),
        h: Math.round(part.h),
        area: Math.round(part.w * part.h),
        frac: Math.round(part.frac * 1000) / 1000,
        descriptions,
        described: descriptions.length > 0,
        natural: naturalOf(el, kind, r),
      });
    }
    ids = new Map();
    generation++;
    const items = readingOrder(found).slice(0, MAX_ITEMS).map((it, i) => {
      const id = `p${i + 1}`;
      ids.set(id, new WeakRef(it.el));
      const { el, ...rest } = it;
      return { id, ...rest };
    });
    return {
      view: { w: vw, h: vh },
      win: {
        screenX, screenY, outerW: outerWidth, outerH: outerHeight, innerW: innerWidth, innerH: innerHeight,
        dpr: devicePixelRatio || 1,
      },
      items,
      gen: generation,
    };
  }

  function lookup(id) {
    const ref = ids.get(String(id));
    const el = ref && ref.deref();
    return el && el.isConnected ? el : null;
  }

  // ------------------------------------------------------------ best source
  // HTML's srcset parsing, simplified: a URL is a run of non-spaces (it may contain commas), then descriptors
  // up to a comma outside parentheses.
  function parseSrcset(text) {
    const s = String(text || "");
    const out = [];
    let i = 0;
    while (i < s.length) {
      while (i < s.length && /[\s,]/.test(s[i])) i++;
      if (i >= s.length) break;
      let j = i;
      while (j < s.length && !/\s/.test(s[j])) j++;
      let url = s.slice(i, j);
      i = j;
      let desc = "";
      if (/,$/.test(url)) url = url.replace(/,+$/, "");
      else {
        let depth = 0;
        while (i < s.length) {
          const c = s[i++];
          if (c === "(") depth++;
          else if (c === ")") depth = Math.max(0, depth - 1);
          else if (c === "," && !depth) break;
          desc += c;
        }
      }
      const cand = { url: abs(url), w: 0, x: 0 };
      for (const d of desc.trim().split(/\s+/)) {
        const m = /^(\d+(?:\.\d+)?)([wx])$/i.exec(d);
        if (m) cand[m[2].toLowerCase()] = parseFloat(m[1]);
      }
      if (!cand.w && !cand.x) cand.x = 1;
      if (cand.url && !/^data:/i.test(cand.url)) out.push(cand); // lazy-loading placeholders aren't pictures
    }
    return out;
  }

  function biggest(cands) {
    const withW = cands.filter((c) => c.w > 0);
    const pool = withW.length ? withW : cands;
    let best = null;
    for (const c of pool) if (!best || (withW.length ? c.w > best.w : c.x > best.x)) best = c;
    return best;
  }

  // srcset, or data-srcset when srcset only holds a lazy-loading placeholder.
  function realSrcset(el) {
    for (const name of ["srcset", "data-srcset"]) {
      const set = el.getAttribute(name) || "";
      if (parseSrcset(set).length) return set;
    }
    return "";
  }

  // The srcset of the <picture> <source> the browser would pick, or "".
  function sourceSrcset(img) {
    const pic = img.parentElement;
    if (!pic || pic.tagName !== "PICTURE") return "";
    for (const src of pic.children) {
      if (src === img) break;
      if (src.tagName !== "SOURCE") continue;
      const type = (src.getAttribute("type") || "").trim();
      if (type && !SUPPORTED.test(type)) continue;
      const media = (src.getAttribute("media") || "").trim();
      if (media && !matchMedia(media).matches) continue;
      const set = realSrcset(src);
      if (set) return set;
    }
    return "";
  }

  // Wikipedia serves small thumbnails (250px-Cat.jpg); the same path at 1280px is a sharp copy. If the
  // original is smaller, the request fails and the viewer keeps the first picture.
  function wikimediaFull(url) {
    const m = /^(https:\/\/(?:upload|thumb)\.wikimedia\.org\/.+\/thumb\/.+\/)(\d+)px-([^/]+)$/.exec(url || "");
    return m && Number(m[2]) < 1280 ? `${m[1]}1280px-${m[3]}` : url;
  }

  function imgSources(img) {
    let first = img.currentSrc || img.src || "";
    let srcset = "";
    const placeholder = !first || /^data:/i.test(first) || (img.complete && img.naturalWidth > 0 && img.naturalWidth < 50);
    if (placeholder) {
      const lazy = img.getAttribute("data-src") || img.getAttribute("data-lazy-src") || img.getAttribute("data-original");
      srcset = img.getAttribute("data-srcset") || "";
      if (lazy && abs(lazy)) first = abs(lazy);
      else if (srcset) {
        const top = biggest(parseSrcset(srcset));
        if (top) first = top.url;
      }
    }
    if (!srcset) srcset = sourceSrcset(img) || realSrcset(img);
    const top = biggest(parseSrcset(srcset));
    const better = wikimediaFull((top && top.url) || first);
    return { first, better: better && better !== first ? better : "" };
  }

  // ------------------------------------------------------------ the picture shown in the viewer
  function copySvgStyles(from, to) {
    const cs = getComputedStyle(from);
    to.style.setProperty("color", cs.color);
    to.style.setProperty("fill", cs.fill);
    to.style.setProperty("stroke", cs.stroke);
    const a = from.querySelectorAll(SVG_SHAPES);
    const b = to.querySelectorAll(SVG_SHAPES);
    const n = Math.min(a.length, b.length, MAX_SHAPES);
    for (let i = 0; i < n; i++) {
      const s = getComputedStyle(a[i]);
      const set = (k) => b[i].style.setProperty(k, s.getPropertyValue(k));
      for (const k of ["fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity", "opacity", "visibility", "display"]) set(k);
      if (a[i].tagName === "text" || a[i].tagName === "tspan") for (const k of ["font-size", "font-family", "font-weight"]) set(k);
    }
  }

  function buildPicture(el, description) {
    const r = el.getBoundingClientRect();
    const rectSize = { w: Math.max(1, r.width), h: Math.max(1, r.height) };
    if (isSvg(el)) {
      const clone = el.cloneNode(true);
      if (!clone.getAttribute("viewBox")) clone.setAttribute("viewBox", `0 0 ${rectSize.w} ${rectSize.h}`);
      for (const a of ["width", "height", "style", "x", "y"]) clone.removeAttribute(a);
      copySvgStyles(el, clone);
      clone.classList.add("pic");
      return { node: clone, natural: rectSize, src: "" };
    }
    if (el.tagName === "IMG") {
      const { first, better } = imgSources(el);
      const img = document.createElement("img");
      img.className = "pic";
      img.alt = description;
      img.draggable = false;
      img.addEventListener("load", () => {
        if (current && current.pic === img && img.naturalWidth > 0) {
          current.natural = { w: img.naturalWidth, h: img.naturalHeight };
          layout(false);
        }
      });
      img.src = first;
      const natural = el.naturalWidth >= 50 && !/^data:/i.test(el.currentSrc || "") ? { w: el.naturalWidth, h: el.naturalHeight } : rectSize;
      return { node: img, natural, src: first, better };
    }
    const url = bgUrl(el);
    const div = document.createElement("div");
    div.className = "pic bg";
    div.setAttribute("role", "img");
    div.setAttribute("aria-label", description);
    div.style.setProperty("background-image", `url(${JSON.stringify(url)})`);
    const probe = new Image();
    probe.onload = () => {
      if (current && current.pic === div && probe.naturalWidth > 0) {
        current.natural = { w: probe.naturalWidth, h: probe.naturalHeight };
        layout(false);
      }
    };
    probe.src = url;
    return { node: div, natural: rectSize, src: url };
  }

  // Loads the bigger candidate in the background; on error the first one stays.
  function upgrade(img, url, token) {
    const pre = new Image();
    pre.onload = () => {
      if (current && current.token === token && current.pic === img && pre.naturalWidth > img.naturalWidth) {
        img.src = url;
        current.src = url;
      }
    };
    pre.src = url;
  }

  // ------------------------------------------------------------ viewer layout
  function bannerBand(vh) {
    const band = { top: 0, bottom: vh };
    const b = document.getElementById("ally-risk-banner");
    if (!b || !P.visible(b)) return band;
    const r = b.getBoundingClientRect();
    if (r.top <= 4 && r.bottom > 0) band.top = Math.min(vh / 2, r.bottom);
    else if (r.bottom >= vh - 4 && r.top < vh) band.bottom = Math.max(vh / 2, r.top);
    return band;
  }

  function clampPan() {
    const v = current;
    const W = v.baseW * v.scale;
    const H = v.baseH * v.scale;
    const a = v.area;
    v.tx = W <= a.w ? a.left + (a.w - W) / 2 : Math.min(a.left, Math.max(a.left + a.w - W, v.tx));
    v.ty = H <= a.h ? a.top + (a.h - H) / 2 : Math.min(a.top, Math.max(a.top + a.h - H, v.ty));
  }

  function apply(animate) {
    const v = current;
    v.pic.classList.toggle("anim", !!animate);
    v.pic.style.setProperty("width", `${v.baseW * v.scale}px`);
    v.pic.style.setProperty("height", `${v.baseH * v.scale}px`);
    v.pic.style.setProperty("transform", `translate(${v.tx}px, ${v.ty}px)`);
    v.stage.classList.toggle("zoomed", v.scale > 1);
    v.zoomIn.setAttribute("aria-disabled", String(v.scale >= SCALES[SCALES.length - 1]));
    v.zoomOut.setAttribute("aria-disabled", String(v.scale <= 1));
  }

  // Fits the picture between the button row and the caption. reset: back to fitted size; otherwise the
  // scale stays and the point at the centre stays at the centre (window resize, page zoom, a new source).
  function layout(reset) {
    const v = current;
    if (!v || !v.pic) return;
    const vw = innerWidth;
    const vh = innerHeight;
    let fx = 0.5;
    let fy = 0.5;
    if (!reset && v.baseW) {
      fx = (v.cx - v.tx) / (v.baseW * v.scale);
      fy = (v.cy - v.ty) / (v.baseH * v.scale);
    }
    const band = bannerBand(vh);
    v.bar.style.setProperty("top", `${band.top}px`);
    v.caption.style.setProperty("bottom", `${vh - band.bottom}px`);
    const top = v.bar.getBoundingClientRect().bottom + 8;
    const capTop = v.caption.hidden ? band.bottom : v.caption.getBoundingClientRect().top;
    const room = Math.max(100, capTop - 8 - top);
    const h = Math.min(room, vh * 0.9);
    const w = vw * 0.9;
    v.area = { left: (vw - w) / 2, top: top + (room - h) / 2, w, h };
    const n = v.natural;
    const k = Math.min(w / Math.max(1, n.w), h / Math.max(1, n.h));
    v.baseW = Math.max(1, n.w) * k;
    v.baseH = Math.max(1, n.h) * k;
    v.cx = v.area.left + w / 2;
    v.cy = v.area.top + h / 2;
    if (reset) v.scale = 1;
    v.tx = v.cx - fx * v.baseW * v.scale;
    v.ty = v.cy - fy * v.baseH * v.scale;
    clampPan();
    apply(false);
  }

  function nextScale(s, dir) {
    if (dir > 0) return SCALES.find((x) => x > s + 1e-3) || SCALES[SCALES.length - 1];
    const lower = SCALES.filter((x) => x < s - 1e-3);
    return lower.length ? lower[lower.length - 1] : 1;
  }

  // One zoom step around (x, y) in viewport px; never below fitted size.
  function zoomAt(dir, x, y) {
    const v = current;
    const s1 = v.scale;
    const s2 = nextScale(s1, dir);
    if (s2 === s1) return;
    const px = x === undefined ? v.cx : x;
    const py = y === undefined ? v.cy : y;
    v.tx = px - ((px - v.tx) * s2) / s1;
    v.ty = py - ((py - v.ty) * s2) / s1;
    v.scale = s2;
    clampPan();
    apply(true);
  }

  function pan(dx, dy) {
    const v = current;
    if (v.scale <= 1) return;
    v.tx += dx;
    v.ty += dy;
    clampPan();
    apply(false);
  }

  // ------------------------------------------------------------ viewer
  function sheet() {
    const s = new CSSStyleSheet();
    s.replaceSync(CSS);
    return s;
  }

  function styleHost(host) {
    const set = (k, v) => host.style.setProperty(k, v, "important");
    set("all", "initial");
    set("position", "fixed");
    set("inset", "0");
    set("z-index", "2147483646");
    set("display", "block");
  }

  function button(text, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    if (cls) b.className = cls;
    return b;
  }

  function buildViewer() {
    const host = document.createElement("div");
    host.id = HOST_ID;
    styleHost(host);
    const root = host.attachShadow({ mode: "open" });
    try {
      root.adoptedStyleSheets = [sheet()];
    } catch {
      const style = document.createElement("style");
      style.textContent = CSS;
      root.append(style);
    }
    const wrap = document.createElement("div");
    wrap.className = "wrap";
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-label", "Picture viewer");
    wrap.tabIndex = -1;
    const stage = document.createElement("div");
    stage.className = "stage";
    const bar = document.createElement("div");
    bar.className = "bar";
    const zoomIn = button("Zoom in (+)");
    const zoomOut = button("Zoom out (−)");
    const prev = button("Previous");
    const next = button("Next");
    const closeBtn = button("Close (Esc)", "close");
    bar.append(zoomIn, zoomOut, prev, next, closeBtn);
    const caption = document.createElement("p");
    caption.className = "caption";
    wrap.append(stage, bar, caption);
    root.append(wrap);

    const v = { host, root, wrap, stage, bar, caption, zoomIn, zoomOut, prev, next, scale: 1, tx: 0, ty: 0,
      baseW: 0, baseH: 0, token: 0, wheel: 0, drag: null, moved: false, order: [] };

    zoomIn.addEventListener("click", () => zoomAt(1));
    zoomOut.addEventListener("click", () => zoomAt(-1));
    prev.addEventListener("click", () => step({ back: true }));
    next.addEventListener("click", () => step({}));
    closeBtn.addEventListener("click", () => close());

    stage.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      v.drag = { x: e.clientX, y: e.clientY, tx: v.tx, ty: v.ty, moved: false, onBackdrop: e.target === stage };
      // Capture on the pressed element, not the stage: a click on the picture must not become a backdrop click.
      try {
        e.target.setPointerCapture(e.pointerId);
      } catch { /* not capturable */ }
    });
    stage.addEventListener("pointermove", (e) => {
      const d = v.drag;
      if (!d) return;
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (!d.moved && Math.hypot(dx, dy) > 5) d.moved = true;
      if (!d.moved || v.scale <= 1) return;
      stage.classList.add("dragging");
      v.tx = d.tx + dx;
      v.ty = d.ty + dy;
      clampPan();
      apply(false);
    });
    const endDrag = () => {
      if (!v.drag) return;
      v.moved = v.drag.moved || !v.drag.onBackdrop;
      v.drag = null;
      stage.classList.remove("dragging");
    };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);
    stage.addEventListener("click", (e) => {
      const moved = v.moved;
      v.moved = false;
      if (!moved && e.target === stage) close();
    });
    stage.addEventListener("dblclick", (e) => {
      if (e.target !== stage) zoomAt(1, e.clientX, e.clientY);
    });
    wrap.addEventListener("wheel", (e) => {
      e.preventDefault();
      const d = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
      if (!d) return;
      if (Math.sign(d) !== Math.sign(v.wheel)) v.wheel = 0;
      v.wheel += d;
      if (Math.abs(v.wheel) < 50) return; // trackpads send many small deltas; a mouse notch is ~100
      const dir = v.wheel < 0 ? 1 : -1;
      v.wheel = 0;
      zoomAt(dir, e.clientX, e.clientY);
    }, { passive: false });
    return v;
  }

  function stop(e) {
    e.stopPropagation();
    e.preventDefault();
  }

  function onKey(e) {
    const v = current;
    if (!v) return;
    const k = e.key;
    if (k === "Escape") {
      stop(e);
      close();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && ["+", "=", "-", "_", "0"].includes(k)) {
      // Ctrl+Plus / Ctrl+Minus: the picture, not Chrome's page zoom, while the viewer covers the page.
      if (k !== "0") zoomAt(k === "+" || k === "=" ? 1 : -1);
      stop(e);
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const by = Math.round(innerWidth * 0.15);
    if (k === "+" || k === "=") zoomAt(1);
    else if (k === "-" || k === "_" || k === "−") zoomAt(-1);
    else if (k === "ArrowLeft") pan(by, 0);
    else if (k === "ArrowRight") pan(-by, 0);
    else if (k === "ArrowUp") pan(0, by);
    else if (k === "ArrowDown") pan(0, -by);
    else if (k === "Tab") {
      // Keep focus on the viewer's buttons while it covers the page.
      const list = Array.from(v.bar.querySelectorAll("button:not([hidden])"));
      const at = list.indexOf(v.root.activeElement);
      const i = at < 0 ? (e.shiftKey ? list.length - 1 : 0) : (at + (e.shiftKey ? -1 : 1) + list.length) % list.length;
      if (list[i]) list[i].focus();
    } else return;
    stop(e);
  }

  function onResize() {
    layout(false);
  }

  function show(el, id, given = "") {
    const v = current;
    const description = String(given || "").trim() || (descriptionsOf(el)[0] || {}).text || "";
    const pic = buildPicture(el, description);
    if (v.pic) v.pic.remove();
    v.token++;
    v.pic = pic.node;
    v.natural = pic.natural;
    v.id = id;
    v.src = pic.src;
    v.description = description;
    v.wheel = 0;
    v.stage.append(pic.node);
    v.caption.textContent = description;
    v.caption.hidden = !description;
    layout(true);
    if (pic.better) upgrade(pic.node, pic.better, v.token);
    return { description, src: pic.src };
  }

  function open({ id, order = [], gen = 0, description = "" } = {}) {
    if (gen && gen !== generation) throw new Error("The page changed. Please ask again."); // ids were renumbered
    const el = lookup(id);
    if (!el) throw new Error(GONE);
    close();
    const stale = document.getElementById(HOST_ID);
    if (stale) stale.remove();

    // WeakRefs now, so a later list() can't make Next / Previous point at other elements.
    const refs = [];
    for (const oid of Array.isArray(order) ? order : []) {
      const ref = ids.get(String(oid));
      if (ref) refs.push({ id: String(oid), ref });
    }
    const prevFocus = document.activeElement;
    const unlock = P.lockScroll();
    current = buildViewer();
    Object.assign(current, { order: refs, prevFocus, unlock });
    current.prev.hidden = refs.length <= 1;
    current.next.hidden = refs.length <= 1;
    // On window, capturing: runs before the reader's document listener, so Esc closes only the picture.
    addEventListener("keydown", onKey, true);
    addEventListener("resize", onResize);
    document.documentElement.appendChild(current.host);
    const shown = show(el, String(id), description);
    current.wrap.focus({ preventScroll: true });
    return { said: "opened the picture", id: String(id), description: shown.description, src: shown.src };
  }

  function zoom({ direction = "in", closeAtFit = false } = {}) {
    if (!current) return { open: false, scale: 1, closed: false };
    if (direction === "out" && current.scale <= 1) {
      if (!closeAtFit) return { open: true, scale: 1, closed: false };
      close();
      return { open: false, scale: 1, closed: true };
    }
    zoomAt(direction === "out" ? -1 : 1);
    return { open: true, scale: current.scale, closed: false };
  }

  function step({ back = false } = {}) {
    const v = current;
    if (!v) return { open: false, id: null, description: "" };
    const n = v.order.length;
    let i = v.order.findIndex((o) => o.id === v.id);
    for (let k = 0; k < n; k++) {
      i = back ? (i <= 0 ? n - 1 : i - 1) : (i + 1) % n;
      const o = v.order[i];
      const el = o.ref.deref();
      if (o.id === v.id || !el || !el.isConnected) continue;
      const shown = show(el, o.id);
      return { open: true, id: o.id, description: shown.description };
    }
    return { open: true, id: v.id, description: v.description };
  }

  function state() {
    return current
      ? { open: true, id: current.id, scale: current.scale, description: current.description || "" }
      : { open: false, id: null, scale: 1, description: "" };
  }

  function close() {
    if (!current) {
      const stale = document.getElementById(HOST_ID);
      if (!stale) return { said: "" };
      stale.remove();
      return { said: "closed the picture" };
    }
    const { host, unlock, prevFocus } = current;
    current = null;
    removeEventListener("keydown", onKey, true);
    removeEventListener("resize", onResize);
    host.remove();
    unlock();
    if (prevFocus && prevFocus.isConnected && typeof prevFocus.focus === "function") prevFocus.focus({ preventScroll: true });
    return { said: "closed the picture" };
  }

  // ------------------------------------------------------------ for Claude (docs/click_by_voice.md, part 2)
  // Numbered yellow boxes around the given pictures, so one screenshot can ask Claude about each. ids [] removes them.
  function outline({ ids: want = [] } = {}) {
    const old = document.getElementById(BOXES_ID);
    if (old) old.remove();
    const numbers = {};
    if (!want.length) return { numbers };
    const host = document.createElement("div");
    host.id = BOXES_ID;
    const set = (k, v) => host.style.setProperty(k, v, "important");
    set("all", "initial");
    set("position", "fixed");
    set("inset", "0");
    set("z-index", "2147483646");
    set("pointer-events", "none");
    const root = host.attachShadow({ mode: "open" });
    let n = 0;
    for (const id of want) {
      const el = lookup(id);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      n++;
      numbers[String(id)] = n;
      const box = document.createElement("div");
      box.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;` +
        "box-sizing:border-box;border:4px solid #ffeb3b;outline:2px solid #000;";
      const tag = document.createElement("div");
      tag.textContent = String(n);
      tag.style.cssText = `position:fixed;left:${Math.max(0, r.left)}px;top:${Math.max(0, r.top)}px;` +
        "background:#ffeb3b;color:#000;font:700 28px/1 Arial,sans-serif;padding:4px 10px;border:2px solid #000;";
      root.append(box, tag);
    }
    document.documentElement.appendChild(host);
    return { numbers };
  }

  // Claude's descriptions, kept for this page by picture source so asking again is instant.
  function remember({ descriptions = {} } = {}) {
    let n = 0;
    for (const [id, text] of Object.entries(descriptions || {})) {
      const el = lookup(id);
      if (el && String(text || "").trim()) {
        claudeSaid.set(sourceKey(el), String(text).trim().slice(0, 300));
        n++;
      }
    }
    return { remembered: n };
  }

  function off() {
    outline({ ids: [] });
    return close();
  }

  P.registerOff("picture", off);
  P.modules.picture = { list, open, zoom, step, state, close, outline, remember };
})();
