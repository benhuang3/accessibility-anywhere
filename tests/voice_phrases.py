"""Labeled spoken answers to "Want me to make this bigger?" (as speech-to-text writes them).

Labels: YES, NO, UNDO, REPEAT, SLOWER, STOP_ASKING, UNCLEAR (AMBIGUOUS or EMPTY: re-ask, never act).
The policy behind the labels comes from Research: Voice > Yes/no and listening rules: never act
on silence or ambiguity, "no problem" is yes, "yeah no" is unclear, "uh-huh" is a weak yes.

Run python tests/voice_phrases.py for accuracy and the safety numbers.
"""

from __future__ import annotations

PHRASES: list[tuple[str, str]] = [
    # --- clear yes
    ("Yes.", "YES"), ("yes", "YES"), ("Yeah.", "YES"), ("Yeah, sure.", "YES"), ("Yep.", "YES"), ("Yup!", "YES"),
    ("Sure.", "YES"), ("Sure thing.", "YES"), ("Okay.", "YES"), ("OK.", "YES"), ("Ok, go ahead.", "YES"),
    ("Please.", "YES"), ("Yes, please.", "YES"), ("Yes please do.", "YES"), ("Go ahead.", "YES"),
    ("Do it.", "YES"), ("Sounds good.", "YES"), ("That sounds good.", "YES"), ("Absolutely.", "YES"),
    ("Definitely.", "YES"), ("Of course.", "YES"), ("Why not?", "YES"), ("Sure, why not.", "YES"),
    ("No problem.", "YES"), ("No worries.", "YES"), ("I don't mind.", "YES"), ("That would be great.", "YES"),
    ("I'd like that.", "YES"), ("Yes, thank you.", "YES"), ("Yeah, thanks.", "YES"), ("Alright.", "YES"),
    ("All right.", "YES"), ("Uh, yes.", "YES"), ("Um, yeah.", "YES"), ("Yes, yes.", "YES"),
    ("Ally, yes.", "YES"), ("Yes, Ally.", "YES"), ("Yes, make it bigger.", "YES"), ("Make it bigger.", "YES"),
    ("Bigger, please.", "YES"), ("Correct.", "YES"), ("I think so.", "YES"), ("You bet.", "YES"),
    ("Go for it.", "YES"), ("Let's do it.", "YES"), ("Yes sir.", "YES"), ("Great.", "YES"), ("Cool.", "YES"),
    ("Fine.", "YES"), ("Mm-hmm.", "YES"), ("Uh-huh.", "YES"), ("Mhm.", "YES"), ("Yeah, go on.", "YES"),
    ("Please do.", "YES"), ("Okay, sure.", "YES"), ("Yes, that's fine.", "YES"), ("Sure, that's fine.", "YES"),
    ("Yeah, I would like that.", "YES"), ("Yes, I do.", "YES"), ("Yeah, please make it bigger.", "YES"),
    # --- clear no
    ("No.", "NO"), ("no", "NO"), ("Nope.", "NO"), ("Nah.", "NO"), ("No thanks.", "NO"), ("No, thank you.", "NO"),
    ("Not now.", "NO"), ("Not right now.", "NO"), ("Not today.", "NO"), ("Leave it.", "NO"),
    ("Leave it as is.", "NO"), ("Don't.", "NO"), ("Please don't.", "NO"), ("Don't do that.", "NO"),
    ("Do not.", "NO"), ("Stop.", "NO"), ("Cancel.", "NO"), ("Never mind.", "NO"), ("Nevermind.", "NO"),
    ("Not really.", "NO"), ("I don't think so.", "NO"), ("I'm fine.", "NO"), ("I'm good.", "NO"),
    ("I'm okay, thanks.", "NO"), ("No need.", "NO"), ("I'd rather not.", "NO"), ("No, no.", "NO"),
    ("Nah, I'm good.", "NO"), ("No, I'm fine.", "NO"), ("Um, no.", "NO"), ("No, Ally.", "NO"), ("No way.", "NO"),
    ("Absolutely not.", "NO"), ("Of course not.", "NO"), ("Not interested.", "NO"), ("Skip it.", "NO"),
    ("No, please.", "NO"), ("Okay, no.", "NO"), ("No, that's okay.", "NO"), ("It's fine, no.", "NO"),
    ("I don't want that.", "NO"), ("Not yet.", "NO"),
    # --- undo
    ("Undo.", "UNDO"), ("Undo that.", "UNDO"), ("Put it back.", "UNDO"), ("Change it back.", "UNDO"),
    ("Go back.", "UNDO"), ("Too big.", "UNDO"), ("That's too much.", "UNDO"), ("Back to normal.", "UNDO"),
    # --- repeat / slower
    ("What?", "REPEAT"), ("What was that?", "REPEAT"), ("Sorry?", "REPEAT"), ("Pardon?", "REPEAT"),
    ("Say that again.", "REPEAT"), ("Can you repeat that?", "REPEAT"), ("Huh?", "REPEAT"),
    ("I didn't catch that.", "REPEAT"), ("Excuse me?", "REPEAT"), ("Come again?", "REPEAT"),
    # --- stop asking (a no that also means: back off for longer)
    ("Stop asking me.", "STOP_ASKING"), ("Please stop asking.", "STOP_ASKING"), ("Leave me alone.", "STOP_ASKING"),
    ("Don't ask me again.", "STOP_ASKING"), ("No more questions.", "STOP_ASKING"), ("Quit asking.", "STOP_ASKING"),
    ("No, stop asking me.", "STOP_ASKING"), ("Yes, stop asking.", "UNCLEAR"),
    ("Slower, please.", "SLOWER"), ("Too fast.", "SLOWER"), ("Slow down.", "SLOWER"),
    ("Can you say it slower?", "SLOWER"), ("Could you talk slower?", "SLOWER"),
    # --- unclear: must never be acted on
    ("Yeah, no.", "UNCLEAR"), ("Yes. No. Wait.", "UNCLEAR"), ("Sure, actually no.", "UNCLEAR"),
    ("Maybe.", "UNCLEAR"), ("I guess.", "UNCLEAR"), ("I guess so.", "UNCLEAR"), ("I suppose.", "UNCLEAR"),
    ("I'm not sure.", "UNCLEAR"), ("I don't know.", "UNCLEAR"), ("Dunno.", "UNCLEAR"), ("Perhaps.", "UNCLEAR"),
    ("Hmm.", "UNCLEAR"), ("Um.", "UNCLEAR"), ("Uh...", "UNCLEAR"), ("", "UNCLEAR"), ("Thank you.", "UNCLEAR"),
    ("Thanks.", "UNCLEAR"), ("You", "UNCLEAR"), ("Bye.", "UNCLEAR"), ("Wait.", "UNCLEAR"),
    ("Hold on.", "UNCLEAR"), ("Let me think.", "UNCLEAR"), ("That's fine.", "UNCLEAR"), ("It's okay.", "UNCLEAR"),
    ("Uh-uh.", "UNCLEAR"), ("Whatever.", "UNCLEAR"), ("Kind of.", "UNCLEAR"), ("Yes, maybe.", "UNCLEAR"),
    ("No, wait, yes.", "UNCLEAR"), ("What's the weather today?", "UNCLEAR"), ("Where are my glasses?", "UNCLEAR"),
    ("My daughter is coming over later.", "UNCLEAR"), ("The cat is on the table.", "UNCLEAR"),
    ("Yes and no.", "UNCLEAR"), ("Either way.", "UNCLEAR"), ("It depends.", "UNCLEAR"),
    ("No, go ahead.", "UNCLEAR"), ("Yeah, don't.", "UNCLEAR"), ("Sure, wait.", "UNCLEAR"),
    # --- speech-to-text spellings and run-ons
    ("yesh", "YES"), ("Yeh.", "YES"), ("Ya.", "YES"), ("YES!", "YES"), ("yes yes yes", "YES"),
    ("Oh, sure.", "YES"), ("Well, okay.", "YES"), ("Okay okay.", "YES"), ("Yeah yeah.", "YES"),
    ("sure go ahead ally", "YES"), ("yes please ally", "YES"), ("Noo.", "NO"), ("Nooo.", "NO"),
    ("Naw.", "NO"), ("no no no", "NO"), ("no thanks ally", "NO"), ("Nope, not now.", "NO"),
    ("What did you say?", "REPEAT"), ("Sorry, what?", "REPEAT"),
    ("Okay, what about the other page and also the email I got yesterday?", "UNCLEAR"),
]


def label(kind: str) -> str:
    return "UNCLEAR" if kind in ("AMBIGUOUS", "EMPTY") else kind


def evaluate() -> dict:
    import os
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from ally.voice_confirm import classify

    rows = []
    for text, want in PHRASES:
        got = label(classify(text, "Want me to make this bigger?").kind)
        rows.append((text, want, got))
    n = len(rows)
    correct = sum(w == g for _t, w, g in rows)
    not_yes = [r for r in rows if r[1] != "YES"]
    false_yes = [r for r in not_yes if r[2] == "YES"]
    yes = [r for r in rows if r[1] == "YES"]
    no = [r for r in rows if r[1] == "NO"]
    false_no = [r for r in rows if r[1] == "YES" and r[2] == "NO"]
    return {
        "phrases": n, "accuracy": round(correct / n, 3),
        "false_yes": len(false_yes), "false_yes_rate": round(len(false_yes) / max(1, len(not_yes)), 3),
        "yes_recall": round(sum(r[2] == "YES" for r in yes) / max(1, len(yes)), 3),
        "no_recall": round(sum(r[2] == "NO" for r in no) / max(1, len(no)), 3),
        "yes_heard_as_no": len(false_no),
        "unclear_kept_unclear": round(sum(r[2] == "UNCLEAR" for r in rows if r[1] == "UNCLEAR")
                                      / max(1, sum(r[1] == "UNCLEAR" for r in rows)), 3),
        "mistakes": [{"text": t, "want": w, "got": g} for t, w, g in rows if w != g],
    }


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate(), indent=2))
