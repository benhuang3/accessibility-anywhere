"""Offline brain: turns common requests into tool calls without any AI.

Used for instant replies ("fast path") and whenever Claude is unreachable.
Each rule has a `full` pattern (the whole request; confident enough to skip Claude)
and a `loose` pattern (found anywhere; only used when offline).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

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
    # Face setup before calibrate. "set up my eyes" and "calibrate" stay on calibrate_eye_tracker,
    # which runs the same flow, so older phrases keep their tool name.
    Rule("face_setup", [("face_setup", {})],
         full=r"(?:please )?(?:set up|setup|scan) (?:my )?face|face (?:setup|set up|scan)|scan my face",
         loose=r"\b(?:set up|setup|scan) (?:my )?face\b|\bface (?:setup|set up)\b|\bscan my face\b"),
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

# Examples also describe strict-only commands, so an unsafe near-match can win and be declined.
EXAMPLES = {
    "stop": ["stop talking", "be quiet", "that's enough talking", "please stop reading now", "no more talking"],
    "reset": ["reset everything", "undo all the changes", "put it back to normal", "reset the page", "undo that"],
    "show_dot": ["show my eye dot", "show where i am looking", "put the eye marker on", "display the gaze circle",
                 "let me see the eye pointer"],
    "hide_dot": ["hide my eye dot", "hide where i am looking", "take the eye marker away", "remove the gaze circle",
                 "hide the eye pointer"],
    "follow_slower": ["follow my eyes more slowly", "slow the eye following down", "move the view slower",
                      "the eye following is too fast", "pan a little slower"],
    "follow_faster": ["follow my eyes more quickly", "speed the eye following up", "move the view faster",
                      "the eye following is too slow", "pan a little faster"],
    "look_magnify": ["magnify where i am looking", "make the spot i'm looking at bigger", "zoom in right here",
                     "magnify this spot", "enlarge the words i'm looking at"],
    "face_setup": ["set up my face", "scan my face", "do the face setup", "start setting up my face",
                   "help set up my face"],
    "quick_calibrate": ["quickly calibrate my eyes", "do a quick eye setup", "quick calibration",
                        "recalibrate my eyes quickly", "quickly check the eye tracker"],
    "calibrate": ["calibrate my eyes", "set up my eyes", "calibrate the eye tracker", "start eye calibration",
                  "help set up eye tracking"],
    "where_looking": ["what am i looking at", "tell me where i'm looking", "where are my eyes pointing",
                      "which part am i looking at", "tell me what i'm staring at"],
    "check_eyes": ["check my eye tracker", "check my eyes", "test the eye tracking", "check the gaze tracker",
                   "see if my eye tracker works"],
    "follow_eyes": ["follow my eyes", "make the zoom follow my gaze", "track where my eyes go",
                    "move the magnifier with my eyes", "have the magnifier follow my eyes"],
    "follow_mouse": ["follow my mouse", "make the zoom follow the pointer", "track where my mouse goes",
                     "move the magnifier with the cursor", "have the magnifier follow my mouse"],
    "eyes_off": ["turn the camera off", "stop tracking my eyes", "disable the eye tracker", "switch off the webcam",
                 "stop watching my eyes"],
    "eyes_on": ["turn the camera on", "start tracking my eyes", "enable the eye tracker", "switch on the webcam",
                "start watching my eyes"],
    "simple_off_all": ["turn simple mode off everywhere", "stop simple mode on every site",
                       "remove simple mode from all sites", "disable simple mode everywhere",
                       "leave simple mode on all websites"],
    "simple_off": ["turn simple mode off", "leave simplified mode", "exit the simple view", "stop simple mode",
                   "switch out of simple mode"],
    "simple_on": ["turn simple mode on", "make this website simpler", "this site is confusing",
                  "this website is too busy", "simplify this website"],
    "buttons": ["make the buttons bigger", "these buttons are too tiny", "i keep missing the buttons",
                "enlarge these buttons", "i need larger buttons"],
    "clutter_off": ["show the ads again", "bring back the pop ups", "show the clutter again", "unhide the ads",
                    "put the cookie banners back"],
    "clutter": ["remove these pop ups", "get these ads out of the way", "hide all the adverts",
                "there are too many pop ups", "clean up the clutter on this page"],
    "menus_off": ["close the big menu", "hide the ally menu", "put the simple menu away", "close this simple menu",
                  "take the big menu away"],
    "menus": ["make the menu easier to use", "this menu keeps disappearing", "show the big menu",
              "simplify this menu", "i can't use this menu"],
    "check_layout": ["check this page layout", "is something cut off", "does this page look okay",
                     "check if the page fits", "is the text getting cut off"],
    "zoom_in": ["zoom in a bit", "zoom in a little more", "zoom in on this page", "bring the page closer with zoom",
                "increase the page zoom"],
    "zoom_out": ["zoom out a bit", "zoom out a little more", "zoom out on this page", "reduce the page zoom",
                 "decrease the page zoom"],
    "text_bigger": ["make the words bigger", "i can't read this", "i can't see this", "make it bigger",
                    "the writing is too tiny", "i need bigger letters", "enlarge this text a little",
                    "make these words larger"],
    "text_smaller": ["make the words smaller", "the writing is too big", "i need smaller letters",
                     "shrink this text a little", "make these words smaller"],
    "read_selection": ["read the highlighted words to me", "read my selected text aloud",
                       "say the words i highlighted", "read just the selection", "read what i selected"],
    "read_page": ["read this to me", "read it out loud", "read me this article", "say what's written on this page",
                  "read the whole page aloud", "can you read this page for me"],
    "describe": ["describe my screen", "tell me what's on the screen", "what is on my screen",
                 "explain what you see on the screen", "describe what's on the screen"],
    "contrast": ["turn on high contrast", "make the contrast stronger", "use high contrast mode",
                 "i need more contrast", "increase the contrast"],
    "dark": ["turn on dark mode", "use a dark background", "make the page dark", "switch to dark theme",
             "give me dark mode"],
    "spacing": ["put more space between the lines", "spread the words out", "give the text more spacing",
                "space the lines out more", "add more space around the text"],
    "dyslexia": ["turn on the dyslexia font", "use the dyslexia reading font", "switch to dyslexic mode",
                 "give me the dyslexia font", "enable the dyslexia style"],
    "reader": ["make it easier to read", "make this easier to read", "make this page easier to read",
               "give me a clean reading view", "show just the article", "put this in reader mode",
               "switch to reading view"],
    "reader_off": ["turn reader mode off", "close the reading view", "leave reader view",
                   "exit the reader", "take me out of reader mode"],
    "invert": ["invert the colors", "reverse the screen colors", "swap the light and dark colors",
               "turn on inverted colors", "use inverted colours"],
    "grayscale": ["make the screen black and white", "turn on grayscale", "use grey scale mode",
                  "show everything in black and white", "switch to grayscale"],
    "colors_normal": ["put the colors back to normal", "remove the color filter", "use regular colors again",
                      "switch the color filters off", "restore the normal colors"],
    "magnifier_off": ["turn the magnifier off", "close the screen magnifier", "stop magnifying the screen",
                      "switch off screen magnification", "disable the magnifier"],
    "magnifier": ["turn the magnifier on", "open the screen magnifier", "start magnifying the screen",
                  "switch on screen magnification", "enable the magnifier"],
    "scroll_down": ["scroll down a little", "go down", "keep going", "next part", "move down the page",
                    "show me the next part", "go a bit further down"],
    "scroll_up": ["scroll up a little", "go up a bit", "move up the page", "show me the part above",
                  "go a bit further up"],
    "back": ["go back", "take me back", "back to the last page", "return to the previous page",
             "take me to the page before this"],
    "reload": ["refresh this page", "load this page again", "reload the website", "try loading this page again",
               "refresh the current tab"],
    "forward": ["go forward", "take me forward", "forward to the next page", "go forward in the browser",
                "return to the page ahead"],
    "help": ["what can you do", "show me the commands", "what can i ask you", "tell me how to use ally",
             "show me some help"],
    "zoom_picture": ["show me the picture", "open the image", "let me see the photo", "make this picture bigger",
                     "show this photo a little closer", "open up the picture"],
    "close_picture": ["close this picture", "hide the photo", "exit the picture viewer", "put the picture away",
                      "close the image viewer"],
    "next_picture": ["show the next photo", "another picture", "move to the next image", "show a different picture",
                     "next photograph"],
    "previous_picture": ["show the previous photo", "go back to the earlier picture", "show the image before this",
                         "previous photograph", "take me back to the last picture"],
    "describe_picture": ["describe this picture", "tell me about this photo", "what is in the image",
                         "explain this photograph", "what does this picture show"],
    "show_numbers": ["show the numbers", "put numbers on the buttons", "number all the links", "show number labels",
                     "give me numbers to click"],
    "hide_numbers": ["hide the numbers", "remove the number labels", "take the numbers away", "numbers off",
                     "clear the numbers"],
    "click_number": ["click number three", "press seven", "tap number four", "choose number two", "click twenty"],
    "click_text": ["click the sign in button", "press submit", "tap the link", "click continue", "choose the tab"],
}

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
    pic = _picture(t, strict) or _clicks(t, strict)
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


# Explicit allowlist: never infer clicks, numbers, resets, calibration, or screen uploads.
_CLOSEST_NAMES = {
    "stop", "show_dot", "hide_dot", "follow_slower", "follow_faster", "look_magnify", "where_looking",
    "follow_eyes", "follow_mouse", "eyes_off", "eyes_on", "simple_off", "simple_on", "buttons", "clutter_off",
    "clutter", "menus_off", "menus", "check_layout", "zoom_in", "zoom_out", "text_bigger", "text_smaller",
    "read_selection", "read_page", "contrast", "dark", "spacing", "dyslexia", "reader", "reader_off", "invert",
    "grayscale", "colors_normal", "magnifier_off", "magnifier", "scroll_down", "scroll_up", "back", "reload",
    "forward", "help",
}
_CLOSEST_ACTIONS = {r.name: r.actions for r in RULES if r.name in _CLOSEST_NAMES}
_CLOSEST_ACTIONS.update({name: actions for name, actions, _ in PICTURE_RULES})
_CLOSEST_ACTIONS["zoom_picture"] = [("zoom_picture", {"about": ""})]
_STOP_WORDS = set("a an the this that these those it me my i you your to of for please can could would "
                  "will do does is are was be have has just bit little really very now thanks thank so um uh "
                  "hey hello hi okay ok".split())
_SENSITIVE = re.compile(r"\b(?:click|press|tap|number\w*|reset|undo|calibrat\w*|setup|set up|send|share|"
                        r"call|tell|pay|buy|purchase|transfer|donate|delete|submit)\b|\d")
_CHATTER = re.compile(r"^(?:why|how|when|who|we|he|she|they)\b|^i (?:read|said|was|did|went|like|had)\b")
_NEGATION = {"not", "don't", "dont", "never", "no", "can't", "cant", "cannot"}
CLOSEST_THRESHOLD = 0.82
CLOSEST_MARGIN = 0.08


def _content(text: str) -> set[str]:
    return set(text.split()) - _STOP_WORDS


def closest(text: str) -> Match | None:
    """Fill a confident, unambiguous paraphrase of a safe command; otherwise leave it to Claude."""
    t = normalize(text)
    if not t or _SENSITIVE.search(t) or _CHATTER.search(t):
        return None
    words = _content(t)
    if not words:
        return None
    scores = []
    for name, examples in _CLOSEST_EXAMPLES.items():
        best = 0.0
        for example, content in examples:
            # Negation changes intent even when almost every other word matches.
            if bool(words & _NEGATION) != bool(content & _NEGATION):
                continue
            overlap = len(words & content) / len(words | content)
            score = 0.55 * SequenceMatcher(None, t, example).ratio() + 0.45 * overlap
            best = max(best, score)
        scores.append((best, name))
    scores.sort(reverse=True)
    (score, name), (runner_up, _) = scores[:2]
    if score < CLOSEST_THRESHOLD or score - runner_up < CLOSEST_MARGIN or name not in _CLOSEST_ACTIONS:
        return None
    return Match(name, [(tool, dict(args)) for tool, args in _CLOSEST_ACTIONS[name]])


_CLOSEST_EXAMPLES = {name: [(normalize(e), _content(normalize(e))) for e in examples]
                     for name, examples in EXAMPLES.items()}


HELP_TEXT = ("You can say: bigger, smaller, reset, read this page, read the selection, stop, high contrast, "
             "dark mode, reader mode, more spacing, magnifier, magnify what I'm looking at, "
             "what am I looking at, check my eye tracker, set up my face, set up my eyes, "
             "follow my eyes slower, follow my eyes faster, show where I am looking, hide the eye dot, "
             "invert colors, scroll down, go back, simple mode, bigger buttons, hide pop-ups, simplify the menu, "
             "check the page, zoom in on the picture, describe the picture, show numbers, click 3, click sign in, "
             "reload, or ask anything when you're online.")
