"""Offline brain: turns common requests into tool calls without any AI.

Used for instant replies ("fast path") and whenever Claude is unreachable.
Each rule has a `full` pattern (the whole request; confident enough to skip Claude)
and a `loose` pattern (found anywhere; only used when offline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FILLER = re.compile(r"^(hey |hi |ok |okay )?(ally |computer )?(please |can you |could you |would you )*|"
                    r"( please| for me| now| thanks| thank you)+$")


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^a-z0-9'%. ]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # keep decimals like 1.5
    t = re.sub(r"\s+", " ", t).strip()
    prev = None
    while prev != t:
        prev = t
        t = FILLER.sub("", t).strip()
    return t


@dataclass
class Rule:
    name: str
    actions: list = field(default_factory=list)  # [(tool, args)]
    full: str | None = None
    loose: str | None = None


RULES = [
    Rule("stop", [("stop_speaking", {})],
         full=r"(stop|stop (it|talking|reading|speaking)|quiet|be quiet|shh+|silence|pause|cancel|shut up)",
         loose=r"\b(stop (talking|reading|speaking)|be quiet|shut up)\b"),
    Rule("reset", [("reset_all", {})],
         full=r"(reset|reset (all|everything|it|this|the page|the screen)|undo( (all|everything|that|it))?|"
              r"back to normal|go back to normal|turn (it|everything) off|normal( view| size)?|panic)",
         loose=r"\b(reset|back to normal|undo (everything|all))\b"),
    Rule("look_magnify", [("magnify_where_i_look", {})],
         full=r"(?:i want |i'd like |let me see |show me )?(?:to )?(?:(?:magnify|enlarge|blow up|zoom(?: in)?(?: on)?|"
              r"make bigger) )?(?:what |where |the (?:thing|part|spot|area|text|word|words) (?:that )?)"
              r"(?:i'm|i am|im) (?:looking|staring|gazing)(?: at)?(?: magnified| bigger| larger| enlarged| zoomed(?: in)?)?|"
              r"(?:magnify|enlarge|zoom(?: in)?(?: on)?) (?:here|right here|this spot|there|where i look)|"
              r"make (?:what|where) (?:i'm|i am|im) looking at (?:bigger|larger)",
         loose=r"\b(?:i'm|i am|im) (?:looking|staring|gazing)\b|\bwhere i look\b|\b(?:magnify|zoom(?: in)?) here\b"),
    Rule("calibrate", [("calibrate_eye_tracker", {})],
         full=r"(?:re)?calibrate(?: the| my)?(?: eyes?| eye ?track(?:er|ing)| gaze(?: tracking)?)?|"
              r"(?:set up|setup)(?: the| my)? (?:eyes|eye ?track(?:er|ing)|gaze(?: tracking)?)|"
              r"(?:eye ?track(?:er|ing)|gaze) (?:calibration|setup|set up)",
         loose=r"\bcalibrat"),
    Rule("where_looking", [("where_am_i_looking", {})],
         full=r"what am i looking at",
         loose=r"\bwhat am i looking at\b"),
    Rule("check_eyes", [("check_eye_tracker", {})],
         full=r"check (?:my |the )?(?:eye ?track(?:er|ing)|eyes)",
         loose=r"\bcheck (?:my |the )?(?:eye ?track(?:er|ing)|eyes)\b"),
    Rule("follow_eyes", [("magnifier_follow", {"target": "eyes"})],
         full=r"(?:follow|track) (?:my )?(?:eyes|gaze)|(?:magnifier|zoom) (?:should )?follows? (?:my )?(?:eyes|gaze)"),
    Rule("follow_mouse", [("magnifier_follow", {"target": "mouse"})],
         full=r"follow (?:my |the )?(?:mouse|pointer|cursor)|"
              r"(?:magnifier|zoom) (?:should )?follows? (?:my |the )?(?:mouse|pointer|cursor)"),
    Rule("eyes_off", [("eye_tracking", {"on": False})],
         full=r"(?:turn off|stop|disable|pause) (?:the )?(?:eye ?track(?:er|ing)|camera|webcam|gaze(?: tracking)?)|"
              r"(?:eye ?track(?:er|ing)|camera|webcam) off"),
    Rule("eyes_on", [("eye_tracking", {"on": True})],
         full=r"(?:turn on|start|enable|resume) (?:the )?(?:eye ?track(?:er|ing)|camera|webcam|gaze(?: tracking)?)|"
              r"(?:eye ?track(?:er|ing)|camera|webcam) on"),
    Rule("zoom_in", [("zoom_in", {})],
         full=r"(zoom in|zoom|bigger|larger|enlarge|increase|make (it|this|that|the text|text|things|everything) "
              r"(bigger|larger)|bigger please|i can't (see|read)( (this|that|it))?|too small|it's too small)",
         loose=r"\b(bigger|larger|zoom in|enlarge|too (small|tiny)|tiny|can't (see|read)|hard to (see|read))\b"),
    Rule("zoom_out", [("zoom_out", {})],
         full=r"(zoom out|smaller|shrink|make (it|this|that|the text|text|things|everything) smaller|too big|"
              r"it's too big)",
         loose=r"\b(smaller|zoom out|too big|shrink)\b"),
    Rule("enlarge_this", [("enlarge_text", {"goal": "bigger"})],
         full=r"(enlarge|blow up) (this|this text|the text)( here| please)?|"
              r"make (this text|the words?) (bigger|larger)|"
              r"make (this|the text) (bigger|larger) (here|in front( of me)?)",
         loose=r"\b(enlarge this|make (this|the text) bigger here)\b"),
    Rule("read_selection", [("read_selection", {})],
         full=r"read (the |my )?(selection|selected( text)?|highlighted( text)?|this text)( to me| aloud| out loud)?",
         loose=r"\bread\b.*\b(selection|selected|highlighted)\b"),
    Rule("read_page", [("read_page", {})],
         full=r"read( (this|the|this web|the web))?( page| article| website| it| this)?( to me| aloud| out loud)?",
         loose=r"\bread\b"),
    Rule("describe", [("describe_screen", {})],
         full=r"(describe|what's on|what is on|look at|read) (my |the )?screen",
         loose=r"\b(describe|what's on|what is on) (my |the )?screen\b"),
    Rule("contrast", [("restyle_page", {"style": "high_contrast"})],
         full=r"(high )?contrast( mode)?( on)?", loose=r"\bcontrast\b"),
    Rule("dark", [("restyle_page", {"style": "dark"})],
         full=r"dark( mode| theme)?( on)?", loose=r"\bdark (mode|theme)\b"),
    Rule("spacing", [("restyle_page", {"style": "spacing"})],
         full=r"(more )?(text )?spacing( on)?|space (it |this )?out", loose=r"\b(more spacing|space (it )?out)\b"),
    Rule("dyslexia", [("restyle_page", {"style": "dyslexia"})],
         full=r"dyslexi[ac]( mode| font)?( on)?", loose=r"\bdyslexi"),
    Rule("reader", [("reader_view", {"on": True})],
         full=r"(reader( mode| view)?|simplify( (this|the) page)?|declutter|clean (it |this )?up)( on)?",
         loose=r"\b(reader (mode|view)|simplify|declutter)\b"),
    Rule("reader_off", [("reader_view", {"on": False})],
         full=r"(close|exit|leave|turn off) (the )?reader( mode| view)?|reader( mode| view)? off"),
    Rule("invert", [("color_filter", {"kind": "invert"})],
         full=r"invert( (the )?colou?rs)?( on)?", loose=r"\binvert\b"),
    Rule("grayscale", [("color_filter", {"kind": "grayscale"})],
         full=r"(gr[ae]y ?scale|black and white)( mode| on)?", loose=r"\b(gr[ae]y ?scale|black and white)\b"),
    Rule("colors_normal", [("color_filter", {"kind": "none"})],
         full=r"(normal|regular) colou?rs|colou?rs? (off|normal)|(turn off|remove) (the )?(colou?r )?filters?"),
    Rule("magnifier_off", [("magnify_screen", {"level": 1.0})],
         full=r"(turn off|stop|close) (the )?(magnifier|magnification|screen zoom)|(magnifier|magnification) off",
         loose=r"\b(magnifier|magnification) off\b"),
    Rule("magnifier", [("toggle_magnifier", {})],
         full=r"(magnifier|magnify( the screen)?|magnification|screen zoom)( on)?", loose=r"\bmagnif(y|ier|ication)\b"),
    Rule("scroll_down", [("scroll_page", {"direction": "down"})],
         full=r"(scroll )?down|scroll|page down|next page|keep going", loose=r"\bscroll down\b"),
    Rule("scroll_up", [("scroll_page", {"direction": "up"})],
         full=r"(scroll )?up|page up|previous page", loose=r"\bscroll up\b"),
    Rule("back", [("browser_navigate", {"action": "back"})], full=r"(go )?back|previous"),
    Rule("forward", [("browser_navigate", {"action": "forward"})], full=r"(go )?forward"),
    Rule("help", [("show_help", {})], full=r"help|what can you do|what can i say|commands"),
]

_COMPILED = [(r, re.compile(rf"^(?:{r.full})$") if r.full else None, re.compile(r.loose) if r.loose else None)
             for r in RULES]

LEVEL = re.compile(r"(?:zoom|magnify|size|scale|make it|set)\D{0,20}?(\d{1,3}(?:\.\d+)?)\s*(%|percent|x|times)")
WORD_LEVELS = {"double": 2.0, "twice": 2.0, "triple": 3.0, "normal size": 1.0}


@dataclass
class Match:
    rule: str
    actions: list


def match(text: str, strict: bool = True) -> Match | None:
    """strict=True: only whole-request matches. strict=False: also keyword matches (offline mode)."""
    t = normalize(text)
    if not t:
        return None
    m = LEVEL.search(t)
    if m:
        value = float(m.group(1))
        level = value / 100.0 if m.group(2) in ("%", "percent") else value
        if 0.25 <= level <= 16:
            if not strict or LEVEL.fullmatch(t) or re.fullmatch(r"(zoom|magnify|set zoom|zoom to|make it)\b.*", t):
                return Match("zoom_to", [("zoom_to", {"level": level})])
    for word, level in WORD_LEVELS.items():
        if re.fullmatch(rf"(zoom |make it |set it )?(to )?{word}( size)?", t):
            return Match("zoom_to", [("zoom_to", {"level": level})])
    for rule, full, loose in _COMPILED:
        if full is not None and full.match(t):
            return Match(rule.name, list(rule.actions))
    if not strict:
        for rule, full, loose in _COMPILED:
            if loose is not None and loose.search(t):
                return Match(rule.name, list(rule.actions))
    return None


HELP_TEXT = ("You can say: bigger, smaller, reset, read this page, read the selection, stop, high contrast, "
             "dark mode, reader mode, more spacing, magnifier, magnify what I'm looking at, "
             "what am I looking at, check my eye tracker, set up my eyes, "
             "invert colors, scroll down, go back, or ask anything when you're online.")
