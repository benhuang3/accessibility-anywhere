"""Voice tools: spoken yes/no questions, answering them, risk warnings and the Cartesia voice list."""

from __future__ import annotations

from typing import Annotated, Literal

from ..registry import ToolError, tool


def _vc(ctx):
    vc = getattr(ctx.agent, "confirm", None)
    if vc is None:
        raise ToolError("Spoken questions aren't available.")
    return vc


@tool(timeout=60)
def ask_yes_no(ctx, question: Annotated[str, "A short yes/no question, 10 words or fewer, e.g. "
                                              "'Want me to make this bigger?'"]):
    """Ask the user a yes/no question out loud and wait for the spoken answer.
    Use it before doing something the user didn't ask for. Returns yes, no, or timeout
    (timeout means no clear answer: do nothing)."""
    ans = _vc(ctx).ask(question, kind="question")
    return {"message": ans.value, "answer": ans.value, "by": ans.by, "silent": True}


@tool(ai=False)
def answer_question(ctx, answer: Literal["yes", "no"]):
    """Answer the question Ally is asking out loud (the on-screen Yes / No buttons)."""
    ok = _vc(ctx).answer(answer, by=ctx.source or "button")
    return {"message": f"Answered {answer}." if ok else "No question is open.", "silent": True}


@tool(ai=False)
def warn_risk(ctx, kind: Literal["scam_popup", "untrusted_site", "remote_access", "payment_field"],
              severity: Literal["low", "medium", "high"] = "high"):
    """Say a calm spoken risk warning and ask the protective question ("Close it?")."""
    ans = _vc(ctx).warn({"kind": kind, "severity": severity})
    return {"message": ans.value if ans is not None else "Warned.", "silent": True}


@tool(ai=False, timeout=30)
def list_voices(ctx, search: Annotated[str, "Part of a voice name, e.g. 'Tessa'. Empty = all."] = ""):
    """List Cartesia voices (name and ID) for speech.cartesia_voice_id in config.toml."""
    from ..speech import cartesia_voices

    voices = cartesia_voices(q=search)
    if not voices:
        return "No Cartesia voices matched."
    return {"message": "; ".join(f"{v['name']} ({v['id']})" for v in voices[:25]), "voices": voices,
            "silent": True}
