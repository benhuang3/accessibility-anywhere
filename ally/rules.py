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
    # The gaze dot comes before look_magnify, which would otherwise take "show me where I'm looking".
    Rule("show_dot", [("show_gaze", {"on": True})],
         full=r"(?:please )?(?:show|display|turn on|put up|bring up)(?: me)? (?:where (?:i'm|i am|im) (?:looking|staring|gazing)(?: at)?|(?:the |my |an |a )?(?:eye|eyes|gaze|eye tracking|eye tracker|looking) (?:dot|circle|marker|pointer|cursor|spot))(?: please)?"),
    Rule("hide_dot", [("show_gaze", {"on": False})],
         full=r"(?:please )?(?:hide|turn off|remove|get rid of|stop showing|don't show|do not show)(?: me)? "
              r"(?:where (?:i'm|i am|im) (?:looking|staring|gazing)(?: at)?|(?:the |my |an |a )?(?:eye|eyes|gaze|eye tracking|eye tracker|looking) (?:dot|circle|marker|pointer|cursor|spot))(?: please)?"),
    Rule("follow_slower", [("eye_follow_speed", {"speed": "slower"})],
         full=r"(?:follow|track|pan|move)(?: (?:my eyes|me|the view|the zoom|the screen))?(?: (?:a bit|a little|much))? (?:slower|more slowly)|slow (?:down )?(?:the )?(?:eye )?(?:following|follow|panning|pan|view)"),
    Rule("follow_faster", [("eye_follow_speed", {"speed": "faster"})],
         full=r"(?:follow|track|pan|move)(?: (?:my eyes|me|the view|the zoom|the screen))?(?: (?:a bit|a little|much))? (?:faster|more quickly|quicker)|speed up (?:the )?(?:eye )?(?:following|follow|panning|pan|view)"),
    Rule("look_magnify", [("magnify_where_i_look", {})],
         full=r"(?:i want |i'd like |let me see |show me )?(?:to )?(?:(?:magnify|enlarge|blow up|zoom(?: in)?(?: on)?|"
              r"make bigger) )?(?:what |where |the (?:thing|part|spot|area|text|word|words) (?:that )?)"
              r"(?:i'm|i am|im) (?:looking|staring|gazing)(?: at)?(?: magnified| bigger| larger| enlarged| zoomed(?: in)?)?|"
              r"(?:magnify|enlarge|zoom(?: in)?(?: on)?) (?:here|right here|this spot|there|where i look)|"
              r"make (?:what|where) (?:i'm|i am|im) looking at (?:bigger|larger)|"
              r"help me read(?: (?:this|that|it|the (?:text|page|screen)))?",
         loose=r"\b(?:i'm|i am|im) (?:looking|staring|gazing)\b|\bwhere i look\b|\b(?:magnify|zoom(?: in)?) here\b"),
    # Quick calibrate comes before calibrate, whose loose pattern would take any "calibrate".
    Rule("quick_calibrate", [("check_eye_tracker", {})],
         full=r"(?:do a )?(?:quick|quickly|fast)(?: re)? ?(?:calibrat(?:e|ion)|check|setup|set up)(?: (?:my|the))?"
              r"(?: eyes?| eye ?track(?:er|ing)| gaze(?: tracking)?)?|"
              r"re ?calibrate(?: (?:my|the))?(?: eyes?| eye ?track(?:er|ing))? (?:quickly|fast)",
         loose=r"\b(?:quick|quickly|fast) ?(?:re ?)?calibrat|\brecalibrate (?:\w+ )*(?:quickly|fast)\b"),
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
    # Eye scrolling (ally/gaze_scroll.py). Before scroll_down/up, whose full patterns would take "scroll".
    Rule("eye_scroll_off", [("eye_scroll", {"on": False})],
         full=r"(?:stop|turn off|no more|don't) (?:eye scrolling|scrolling with my eyes|scroll(?:ing)? with my eyes)|"
              r"eye scroll(?:ing)? off"),
    Rule("eye_scroll_on", [("eye_scroll", {"on": True})],
         full=r"(?:turn on |start )?(?:eye scrolling|scroll(?:ing)? with my eyes)(?: on)?|eye scroll(?:ing)? on|"
              r"let me scroll with my eyes"),
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
    # Page steps (docs/page_steps.md). Before zoom_in and reader so "bigger buttons" and "simplify this
    # site" don't fall into their loose patterns.
    Rule("simple_off_all", [("simplified_mode", {"on": False, "everywhere": True})],
         full=r"(turn off |stop )?simple mode off (everywhere|on (all|every) sites?)|"
              r"(turn off|stop) simple mode (everywhere|on (all|every) sites?)",
         loose=r"\bsimple mode off (everywhere|on (all|every) sites?)\b"),
    Rule("simple_off", [("simplified_mode", {"on": False})],
         full=r"(simple|simplified) mode off|(turn off|stop|exit|leave|end) (the )?(simple|simplified) mode|normal mode",
         loose=r"\b((simple|simplified) mode off|(turn off|stop|exit) (simple|simplified) mode)\b"),
    Rule("simple_on", [("simplified_mode", {"on": True})],
         full=r"(turn on |start )?(simple|simplified) mode( on)?|simplify (this|the) (site|website)|"
              r"make (this|the) (site|website) (simple|simpler|easier)|this (site|website|page) is (too busy|confusing)",
         loose=r"\b((simple|simplified) mode|simplify (this|the) (site|website)|"
               r"make (this|the) (site|website) (simple|simpler))\b"),
    Rule("buttons", [("enlarge_buttons", {"on": True})],
         full=r"make (the )?buttons (bigger|larger)|bigger buttons|enlarge (the )?buttons|"
              r"(the )?buttons are too small|i keep missing (the )?buttons",
         loose=r"\b(bigger buttons|buttons (are )?too small|make the buttons (bigger|larger)|enlarge (the )?buttons)\b"),
    Rule("clutter_off", [("hide_clutter", {"on": False})],
         full=r"show (the )?(clutter|pop[- ]?ups|ads)( again)?|bring back (the )?(pop[- ]?ups|ads)"),
    Rule("clutter", [("hide_clutter", {"on": True})],
         full=r"(hide|remove|get rid of|block|close) (the |all )?(clutter|pop[- ]?ups|ads|adverts|cookie (banners?|notices?))|"
              r"declutter( (this|the) page)?|too many (pop[- ]?ups|ads)|clean up (this|the) page",
         loose=r"\b(declutter|(hide|get rid of) (the |all )?(clutter|pop[- ]?ups|ads)|too many (pop[- ]?ups|ads))\b"),
    Rule("menus_off", [("simplify_menus", {"on": False})],
         full=r"close (the )?(ally |big |simple )menu"),
    Rule("menus", [("simplify_menus", {"on": True})],
         full=r"simplify (the |this )?menus?|(i )?can'?t use (this|the) menu|(the )?menu keeps (closing|disappearing)|"
              r"(open |show )?(the )?(ally|big|simple) menu",
         loose=r"\b(simplify (the |this )?menus?|can'?t use (this|the) menu|menu keeps (closing|disappearing))\b"),
    Rule("check_layout", [("check_page_layout", {})],
         full=r"check (the )?(page|layout|page layout)|does (this|the) page look (right|ok|okay)|is (anything|something) cut off",
         loose=r"\b(check (the )?(page )?layout|is (anything|something) cut off)\b"),
    # Only an explicit "zoom" zooms; "bigger", "too small" and the like make the text itself bigger.
    Rule("zoom_in", [("zoom_in", {})],
         full=r"zoom( in)?( please)?|zoom in (more|again)",
         loose=r"\bzoom in\b"),
    Rule("zoom_out", [("zoom_out", {})],
         full=r"zoom out( please)?|zoom out (more|again)",
         loose=r"\bzoom out\b"),
    Rule("text_bigger", [("enlarge_text", {"goal": "bigger"})],
         full=r"(bigger|larger|enlarge|increase|make (it|this|that|the text|text|things|everything|the words?|this text) "
              r"(bigger|larger)( here| please| in front( of me)?)?|bigger please|i can't (see|read)( (this|that|it))?|"
              r"too small|it's too small|(enlarge|blow up) (this|this text|the text)( here| please)?|"
              r"(bigger|larger) (text|font|letters|writing))",
         loose=r"\b(bigger|larger|enlarge|too (small|tiny)|tiny|can't (see|read)|hard to (see|read))\b"),
    Rule("text_smaller", [("enlarge_text", {"goal": "smaller"})],
         full=r"(smaller|shrink|make (it|this|that|the text|text|things|everything) smaller|too big|it's too big|"
              r"smaller (text|font))",
         loose=r"\b(smaller|too big|shrink)\b"),
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
         full=r"(reader( mode| view)?|simplify( (this|the) page)?|clean (it |this )?up)( on)?",
         loose=r"\b(reader (mode|view)|simplify)\b"),
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
    Rule("reload", [("browser_navigate", {"action": "reload"})],
         full=r"(reload|refresh)( (the|this) (page|website|site|tab))?|load (the |this )?page again"),
    Rule("forward", [("browser_navigate", {"action": "forward"})], full=r"(go )?forward"),
    Rule("help", [("show_help", {})], full=r"help|what can you do|what can i say|commands"),
]

_COMPILED = [(r, re.compile(rf"^(?:{r.full})$") if r.full else None, re.compile(r.loose) if r.loose else None)
             for r in RULES]

# "Zoom in on the picture (of X)" (docs/picture_viewer.md). A picture word is required, so plain "zoom in" keeps
# zooming the page. Checked before the rules: "zoom in on..." would otherwise reach zoom_in's loose pattern.
_PIC = r"(?:picture|photo|photograph|image|pic|painting|drawing|illustration)s?"
_PIC_VERB = (r"(?:zoom(?: in| into)?(?: on| to)?|magnify|enlarge|blow up|open|show(?: me)?|let me see|"
             r"make|look at|see)")
_PIC_ABOUT = r"(?: (?:of|with|showing|about) (?P<about>[a-z0-9' ]+?))?"
_PIC_TAIL = r"(?: (?:bigger|larger|up|closer|bigger please|for me))?"
# "open", "show me" and "see" need "the/that/this" before the picture ("open google photos" and "show me photos of
# my grandkids on facebook" are about apps and sites, not a picture on screen).
_PIC_ZOOM_VERB = r"(?:zoom(?: in| into)?(?: on| to)?|magnify|enlarge|blow up)"
_PIC_LOOK_VERB = r"(?:open|show(?: me)?|let me see|make|look at|see)"
PICTURE = re.compile(rf"(?:{_PIC_ZOOM_VERB}(?: (?:the|that|this|a|an|your))?|{_PIC_LOOK_VERB} (?:the|that|this))"
                     rf" (?:(?!out\b)(?P<before>[a-z0-9' ]+?) )?{_PIC}{_PIC_ABOUT}{_PIC_TAIL}")
PICTURE_LOOSE = re.compile(rf"\b(?:zoom(?! out)|magnify|enlarge|blow up)\b.*?\b{_PIC}\b(?: (?:of|with|showing) "
                           rf"(?P<about>[a-z0-9' ]+))?")
PICTURE_RULES = [
    ("zoom_out", [("zoom_out", {})], re.compile(rf"zoom out (?:of|on|from) (?:the |this |that )?{_PIC}")),
    ("close_picture", [("close_picture", {})],
     re.compile(rf"(?:close|exit|hide|leave) (?:the |this |that )?(?:{_PIC}|picture viewer|viewer)")),
    ("next_picture", [("next_picture", {})],
     re.compile(rf"(?:show (?:me )?)?(?:the )?(?:next|another|other|different) {_PIC}")),
    ("previous_picture", [("next_picture", {"back": True})],
     re.compile(rf"(?:show (?:me )?|go back to )?(?:the )?(?:previous|earlier) {_PIC}|(?:the )?{_PIC} before")),
]


DESCRIBE_PICTURE = re.compile(
    rf"(?:describe|tell me about|explain)(?: the| this| that| a)? (?:(?P<before>[a-z0-9' ]+?) )?{_PIC}{_PIC_ABOUT}|"
    rf"what(?:'s| is| does)(?: in| on)?(?: the| this| that)? {_PIC}(?: show(?:ing)?| of)?"
    + _PIC_ABOUT.replace("?P<about>", "?P<about2>") + "|" +
    rf"what(?:'s| is) (?:in|on) (?:the |this |that )?{_PIC}")


def _picture(t: str, strict: bool) -> Match | None:
    m = DESCRIBE_PICTURE.fullmatch(t)
    if m:
        g = m.groupdict()
        about = (g.get("about") or g.get("about2") or g.get("before") or "").strip()
        return Match("describe_picture", [("describe_picture", {"about": about})])
    for name, actions, pattern in PICTURE_RULES:
        if pattern.fullmatch(t):
            return Match(name, [(tool, dict(args)) for tool, args in actions])
    m = PICTURE.fullmatch(t) or (None if strict else PICTURE_LOOSE.search(t))
    if not m:
        return None
    groups = m.groupdict()
    about = (groups.get("about") or "").strip()
    before = (groups.get("before") or "").strip()
    if not about and before and before not in ("big", "bigger", "large", "small", "little"):
        about = before
    return Match("zoom_picture", [("zoom_picture", {"about": about})])


# Click by voice / by number (docs/click_by_voice.md). After the picture phrases ("open the picture").
_SMALL = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
          "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_NUM = (r"(?:\d{1,2}|(?:" + "|".join(_TENS) + r")(?:[ -](?:" + "|".join(_SMALL[:9]) + r"))?|"
        + "|".join(sorted(_SMALL, key=len, reverse=True)) + ")")
CLICK_RULES = [
    ("hide_numbers", re.compile(r"(?:hide|turn off|remove|clear|get rid of)(?: the| all)?(?: the)? numbers|"
                                r"numbers off|no (?:more )?numbers")),
    ("show_numbers", re.compile(r"(?:show|turn on|display|put up|give me)(?: me)?(?: the)? (?:numbers|number labels)"
                                r"(?: on (?:the |this )?(?:buttons|links|page|screen|site))?|"
                                r"number (?:the |all the )?(?:buttons|links|page|things)|numbers on|"
                                r"show (?:me )?(?:the |all the )?(?:links|buttons)|(?:show me )?what can i click(?: on)?")),
]
CLICK_NUMBER = re.compile(rf"(?:(?:click|press|tap|choose|select|pick|hit)(?: on)?(?: number)? |number )(?P<n>{_NUM})")
# A number on its own ("seven") only offline: online it may answer something Claude asked ("how many?"), and
# Claude sees "Which number?" in the conversation and calls click_number itself.
BARE_NUMBER = re.compile(rf"(?P<n>{_NUM})")
CLICK_TEXT = re.compile(r"(?:click|press|tap|hit)(?: on)? (?P<label>.+)|"
                        r"(?:open|choose|select) (?:the )?(?!(?:a |another )?new (?:tab|window))"
                        r"(?P<label2>[a-z0-9' ]+?) (?:link|button|tab)")
CLICK_LOOSE = re.compile(r"\b(?:click|press|tap)(?: on)? (?P<label>[a-z0-9' ]+)")


def _number_value(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    parts = re.split(r"[ -]", word)
    if parts[0] in _TENS:
        return 20 + 10 * _TENS.index(parts[0]) + (_SMALL.index(parts[1]) + 1 if len(parts) > 1 else 0)
    return _SMALL.index(word) + 1 if word in _SMALL else None


# Tabs (docs/tabs_and_pointing.md).
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10}
TAB_RULES = [
    ({"which": "other"}, re.compile(r"(?:go (?:back )?to |switch to |take me to )?(?:the |my )?(?:other|last|previous used|"
                                    r"last used) tab|switch (?:the )?tabs?|go back to (?:the |my )?(?:last|other|previous) tab")),
    ({"which": "next"}, re.compile(r"(?:go to |switch to )?(?:the )?next tab")),
    ({"which": "previous"}, re.compile(r"(?:go to |switch to )?(?:the )?(?:previous tab|tab before(?: this(?: one)?)?)")),
    ({"which": "first"}, re.compile(r"(?:go to |switch to )?(?:the )?first tab")),
]
TAB_NUMBER = re.compile(rf"(?:go to |switch to |open )?(?:the )?(?:tab (?:number )?(?P<n>{_NUM})|"
                        rf"(?P<o>{'|'.join(_ORD)}) tab)")
TAB_NAME = re.compile(r"(?:go to|switch to|take me to|show me|open)(?: the| my)? (?P<name>[a-z0-9' ]+?) tab")
LIST_TABS = re.compile(r"(?:what|which) tabs (?:are|do i have)(?: open)?|list (?:my |the |all )?(?:open )?tabs|"
                       r"(?:read|tell me) (?:my |the )?tabs|how many tabs(?: are open| do i have)?")
# "Show me where to click" and highlighting.
WHERE = re.compile(r"where (?:do|can|should) i (?:click|press|tap)(?: on)?(?: to| for)? (?P<a>.+)|"
                   r"where(?:'s| is) (?:the |my )?(?P<b>.+?)(?: button| link| box)?|"
                   r"(?:point (?:to|at)|find|show me) (?:the |my )?(?P<c>[a-z0-9' ]+?) (?:button|link|box|field)|"
                   r"point (?:to|at) (?:the |my )?(?P<d>.+)")
WHERE_ALL = re.compile(r"(?:show me )?where (?:do |can |should )?i (?:click|press|tap)|show me where to (?:click|press|tap)")
HIGHLIGHT = re.compile(r"highlight (?:the |this |my )?(?P<t>.+)")
HIGHLIGHT_OFF = re.compile(r"(?:stop|turn off) highlighting|(?:clear|remove|hide|turn off) (?:the |all )?highlights?|"
                           r"no (?:more )?highlights?")


def _tabs(t: str) -> Match | None:
    for args, pattern in TAB_RULES:
        if pattern.fullmatch(t):
            return Match("switch_tab", [("switch_tab", dict(args))])
    m = TAB_NUMBER.fullmatch(t)
    if m:
        n = _ORD.get(m.group("o") or "") or _number_value(m.group("n") or "")
        if n:
            return Match("switch_tab", [("switch_tab", {"number": n})])
    if LIST_TABS.fullmatch(t):
        return Match("list_tabs", [("list_tabs", {})])
    m = TAB_NAME.fullmatch(t)
    if m and m.group("name").strip() not in ("new", "a new", "another", "another new"):
        return Match("switch_tab", [("switch_tab", {"name": m.group("name").strip()})])
    return None


def _pointing(t: str) -> Match | None:
    if HIGHLIGHT_OFF.fullmatch(t):
        return Match("clear_highlight", [("clear_highlight", {})])
    if WHERE_ALL.fullmatch(t):
        return Match("show_numbers", [("show_numbers", {"on": True})])
    m = HIGHLIGHT.fullmatch(t)
    if m:
        target = m.group("t").strip()
        if target in ("links", "the links", "all links", "all the links"):
            return Match("highlight_links", [("restyle_page", {"style": "highlight_links"})])
        return Match("highlight_area", [("highlight_area", {"target": target})])
    m = WHERE.fullmatch(t)
    if m:
        target = next((g for g in m.groups() if g), "").strip()
        if target and not re.match(r"(?:i'm|i am|im) |am i\b", target):
            return Match("show_where", [("show_where", {"target": target})])
    return None


def _clicks(t: str, strict: bool) -> Match | None:
    for name, pattern in CLICK_RULES:
        if pattern.fullmatch(t):
            return Match(name, [("show_numbers", {"on": name == "show_numbers"})])
    m = CLICK_NUMBER.fullmatch(t) or (None if strict else BARE_NUMBER.fullmatch(t))
    if m:
        n = _number_value(m.group("n"))
        if n and 1 <= n <= 99:
            return Match("click_number", [("click_number", {"n": n})])
    m = CLICK_TEXT.fullmatch(t) or (None if strict else CLICK_LOOSE.search(t))
    if m:
        label = (m.groupdict().get("label") or m.groupdict().get("label2") or "").strip()
        if label:
            return Match("click_text", [("click_text", {"label": label})])
    return None


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
    pic = _picture(t, strict) or _tabs(t) or _pointing(t) or _clicks(t, strict)
    if pic:
        return pic
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
             "follow my eyes slower, follow my eyes faster, scroll with my eyes, show where I am looking, hide the eye dot, "
             "invert colors, scroll down, go back, simple mode, bigger buttons, hide pop-ups, simplify the menu, "
             "check the page, zoom in on the picture, describe the picture, show numbers, click 3, click sign in, "
             "where do I click to sign in, highlight the menu, go to the other tab, next tab, list my tabs, "
             "reload, or ask anything when you're online.")
