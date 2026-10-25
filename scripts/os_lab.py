"""Test Ally's OS-level automation on this computer, and nothing else.

    python scripts/os_lab.py            every step; after each visible one you answer y / n / s(kip)
    python scripts/os_lab.py --auto     only the checks that change nothing on screen
    python scripts/os_lab.py --yes      every step, no questions (visible steps are left "unconfirmed")
    python scripts/os_lab.py --only magnify_2x,card --list

No agent, server, Claude, camera, microphone, senses, voice or risk code runs, so port 8765 stays free.
Tools are called through the real registry, which runs each one on its own thread like the agent does.
The screen is always put back at the end (magnifier off, colors normal, cards closed, text restored).
A JSON report goes to logs/os_lab/.
"""

from __future__ import annotations

import argparse
import json
import platform as pyplatform
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# Running as a script: the repo root has to be importable.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ally.platforms import get_platform, win_uia
from ally.registry import Registry, ToolError, collect_tools
from ally.tools import screen_tools, system_tools, ui_text_tools

ON_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------- a ctx without the agent
class _State:
    def __init__(self):
        self.data: dict = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class _NoBrowser:
    connected = False
    focused = False
    tab: dict = {}

    def call(self, action, timeout=8.0, **args):
        raise ToolError("Chrome isn't part of the OS lab.")


class LabCtx:
    def __init__(self, os_, registry: Registry):
        self.os = os_
        self.registry = registry
        self.state = _State()
        self.browser = _NoBrowser()
        self.config = {"agent": {"zoom_target": "screen"}}
        self.source = "os_lab"
        self.depth = 0
        self.agent = None
        self.said: list[str] = []

    def say(self, text: str, wait: bool = False) -> bool:
        self.said.append(text)
        print(f"    (Ally says) {text}")
        try:
            u = self.os.tts_start(text, 1.0, "")
            if wait:
                u.wait(30)
        except Exception as e:
            print(f"    (speech failed: {e})")
        return True

    def stop_speaking(self) -> None:
        pass

    def run(self, tool_name: str, **args):
        return self.registry.call(tool_name, args, ctx=self)

    def event(self, text: str, kind: str = "plugin") -> None:
        print(f"    [{kind}] {text}")


# ---------------------------------------------------------------- steps
class Lab:
    def __init__(self, mode: str):
        self.mode = mode  # ask | yes | auto
        self.os = get_platform()
        self.registry = Registry()
        for module in (screen_tools, system_tools, ui_text_tools):
            for t in collect_tools(module):
                self.registry.add(t)
        self.ctx = LabCtx(self.os, self.registry)
        self.results: list[dict] = []
        self.notepad_box = None

    # helpers
    def tool(self, name: str, **args):
        out = self.registry.call(name, args, ctx=self.ctx)
        if not out.ok:
            raise ToolError(out.error)
        return out.value

    def screen_size(self) -> tuple[int, int]:
        if ON_WINDOWS:
            import ctypes

            u = ctypes.windll.user32
            return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        return 1920, 1080

    def pause(self, text: str) -> None:
        """Ask the person to set something up before a step (skipped in --yes mode)."""
        if self.mode == "ask":
            input(f"    >> {text} Then press Enter. ")
        else:
            print(f"    >> {text}")
            time.sleep(2.5)

    def confirm(self, question: str) -> str:
        if self.mode != "ask":
            time.sleep(2.0)  # long enough for someone watching to see it
            return "unconfirmed"
        while True:
            a = input(f"    ?? {question} [y/n/s] ").strip().lower()[:1]
            if a in ("y", "n", "s"):
                return {"y": "pass", "n": "fail", "s": "skipped"}[a]

    # ---- checks that change nothing
    def s_env(self):
        mods = {}
        for m in ("comtypes", "pynput", "mss", "PIL", "tkinter"):
            try:
                __import__(m)
                mods[m] = "ok"
            except Exception as e:
                mods[m] = f"missing ({type(e).__name__})"
        return {"python": sys.version.split()[0], "bits": struct.calcsize("P") * 8,
                "os": f"{pyplatform.system()} {pyplatform.release()} {pyplatform.version()}",
                "machine": pyplatform.machine(), "adapter": self.os.name, "modules": mods}

    def s_checks(self):
        checks = self.os.checks()
        bad = [c[1] for c in checks if c[0] == "fail"]
        return {"checks": checks, "_status": "fail" if bad else "pass", "why": ", ".join(bad)}

    def s_dpi(self):
        if not ON_WINDOWS:
            return {"_status": "skipped", "why": "Windows only"}
        from ally.platforms.windows import set_dpi_aware

        w, h = self.screen_size()
        return {"dpi_aware": set_dpi_aware(), "screen": [w, h]}

    def s_uia_available(self):
        ok, why = win_uia.available()
        return {"available": ok, "why": why, "_status": "pass" if ok else ("fail" if ON_WINDOWS else "skipped")}

    def s_uia_focus_threads(self):
        """The same call from this thread and from a fresh thread that never set up COM (like a tool)."""
        if not ON_WINDOWS:
            return {"_status": "skipped", "why": "Windows only"}
        main = win_uia.focused_element()
        box = {}
        t = threading.Thread(target=lambda: box.setdefault("el", win_uia.focused_element()))
        t.start()
        t.join(10)
        other = box.get("el")
        return {"main_thread": main, "fresh_thread": other,
                "_status": "pass" if (main is not None and other is not None) else "fail"}

    def s_uia_point(self):
        if not ON_WINDOWS:
            return {"_status": "skipped", "why": "Windows only"}
        import ctypes
        import ctypes.wintypes as wt

        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        el = win_uia.element_from_point(pt.x, pt.y)
        return {"cursor": [pt.x, pt.y], "element": el, "_status": "pass" if el else "fail"}

    def s_frontmost(self):
        app = self.os.frontmost_app()
        return {"frontmost": app, "_status": "pass" if app else "fail"}

    def s_screenshot(self):
        png = self.os.screenshot_png()
        return {"bytes": len(png), "_status": "pass" if len(png) > 1000 else "fail"}

    # ---- visible steps
    def s_magnify_2x(self):
        msg = self.tool("magnify_screen", level=2.0)
        return {"message": msg, "_ask": "Is the whole screen zoomed to about 2x? Move the mouse: does the view follow it?"}

    def s_zoom_steps(self):
        a = self.tool("zoom_in")
        b = self.tool("zoom_in")
        c = self.tool("zoom_out")
        return {"messages": [a, b, c], "level": self.os.magnify_level,
                "_ask": "Did it zoom in twice, then out once (ending a step above 2x)?"}

    def s_magnify_center(self):
        w, h = self.screen_size()
        msg = self.os.magnify_at(2.0, w / 2, h / 2, follow="none")
        return {"message": msg, "view": self.os.view(),
                "_ask": "Is the view centered on the middle of the screen, and still when you nudge the mouse?"}

    def s_magnify_off(self):
        msg = self.tool("magnify_screen", level=1.0)
        return {"message": msg, "_ask": "Is the screen back to normal size?"}

    def s_colors(self):
        out = {}
        for kind, q in (("invert", "Are the colors inverted?"), ("grayscale", "Is the screen grayscale?")):
            try:
                out[kind] = self.tool("color_filter", kind=kind)
            except ToolError as e:
                out[kind] = f"error: {e}"
            if self.mode == "ask":
                out[f"{kind}_seen"] = self.confirm(q)
            else:
                time.sleep(1.5)
        out["none"] = self.tool("color_filter", kind="none")
        return dict(out, _ask="Are the colors back to normal?")

    def s_card(self):
        msg = self.os.show_text_overlay(None, "This is Ally's reading card. Big, clear words on top of every window.")
        return {"message": msg, "_ask": "Is a big yellow card with dark text in the middle of the screen, on top?",
                "_after": self.os.clear_text_overlays}

    def s_notepad_focus(self):
        if not ON_WINDOWS:
            return {"_status": "skipped", "why": "Windows only"}
        subprocess.Popen(["notepad.exe"])
        self.pause("Notepad is opening. Click inside its text area so it has the cursor.")
        box = self.os.read_focused_box()
        self.notepad_box = box
        if not box:
            return {"box": None, "_status": "fail", "why": "UI Automation found no focused text box"}
        out = self.tool("enlarge_text", goal="bigger")
        return {"box": box, "enlarge": out, "_ask": "Did the screen zoom in centered on Notepad's text area?",
                "_after": lambda: self.tool("reset_all")}

    def s_notepad_rewrite(self):
        if not ON_WINDOWS:
            return {"_status": "skipped", "why": "Windows only"}
        self.pause("Click inside Notepad's text area (an empty or short document).")
        out = self.tool("enlarge_text", goal="Hello from Ally")
        if out.get("tier") != "native-rewrite":
            return {"enlarge": out, "_ask": "Notepad couldn't be edited in place; is a reading card showing instead?",
                    "_after": self.os.clear_text_overlays}
        seen = self.confirm("Does Notepad now say 'Hello from Ally'?")
        back = self.tool("restore_ui_text")
        return {"enlarge": out, "rewrite_seen": seen, "restore": back,
                "_ask": "Is Notepad's text back to what it was before?"}

    def s_card_near_box(self):
        box = self.notepad_box or (200, 200, 600, 300)
        msg = self.os.show_text_overlay(box, "This card should sit just below the text you were working in.")
        return {"box": box, "message": msg, "_ask": "Is the card next to (below or above) Notepad's text area?",
                "_after": self.os.clear_text_overlays}

    def s_read_selection(self):
        self.pause("Select a few words in any app (Notepad is fine).")
        msg = self.tool("read_selection")
        return {"message": msg, "said": self.ctx.said[-1:], "_ask": "Did Ally speak the selected words?"}

    STEPS = [
        ("env", "auto"), ("checks", "auto"), ("dpi", "auto"), ("uia_available", "auto"),
        ("uia_focus_threads", "auto"), ("uia_point", "auto"), ("frontmost", "auto"), ("screenshot", "auto"),
        ("magnify_2x", "visual"), ("zoom_steps", "visual"), ("magnify_center", "visual"), ("magnify_off", "visual"),
        ("colors", "visual"), ("card", "visual"), ("notepad_focus", "visual"), ("notepad_rewrite", "visual"),
        ("card_near_box", "visual"), ("read_selection", "visual"),
    ]

    # ---- running
    def run_step(self, name: str) -> dict:
        print(f"\n== {name}")
        t0 = time.monotonic()
        rec: dict = {"step": name}
        try:
            data = getattr(self, f"s_{name}")() or {}
            after = data.pop("_after", None)
            question = data.pop("_ask", None)
            status = data.pop("_status", None)
            rec.update(data)
            if question:
                status = self.confirm(question)
            if after:
                after()
            rec["status"] = status or "pass"
        except Exception as e:
            rec.update(status="error", error=f"{type(e).__name__}: {e}")
        rec["seconds"] = round(time.monotonic() - t0, 2)
        shown = {k: v for k, v in rec.items() if k not in ("step",)}
        print("    " + json.dumps(shown, default=str)[:600])
        self.results.append(rec)
        return rec

    def cleanup(self) -> None:
        for fn in (lambda: self.tool("restore_ui_text"), lambda: self.os.magnify(1.0),
                   lambda: self.os.color_filter("none"), self.os.shutdown):
            try:
                fn()
            except Exception as e:
                print(f"    (cleanup: {e})")

    def report(self) -> Path:
        folder = _ROOT / "logs" / "os_lab"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / time.strftime("os_lab-%Y%m%d-%H%M%S.json")
        path.write_text(json.dumps({"mode": self.mode, "results": self.results}, indent=1, default=str),
                        encoding="utf-8")
        return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Test Ally's OS-level automation only.")
    ap.add_argument("--auto", action="store_true", help="only checks that change nothing on screen")
    ap.add_argument("--yes", action="store_true", help="run every step without asking")
    ap.add_argument("--only", default="", help="comma-separated step names")
    ap.add_argument("--list", action="store_true", help="list the steps and exit")
    a = ap.parse_args(argv)
    if a.list:
        for name, kind in Lab.STEPS:
            print(f"{name:20} {kind}")
        return 0
    mode = "auto" if a.auto else ("yes" if a.yes else "ask")
    lab = Lab(mode)
    wanted = [s for s in a.only.split(",") if s] or [n for n, k in Lab.STEPS if mode != "auto" or k == "auto"]
    unknown = [s for s in wanted if not hasattr(lab, f"s_{s}")]
    if unknown:
        print(f"Unknown step(s): {', '.join(unknown)}. Use --list.")
        return 2
    print(f"Ally OS lab: {len(wanted)} step(s), mode {mode}, adapter {lab.os.name}.")
    try:
        for name in wanted:
            lab.run_step(name)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        lab.cleanup()
    path = lab.report()
    counts: dict[str, int] = {}
    for r in lab.results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("\n" + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    for r in lab.results:
        if r["status"] in ("fail", "error"):
            print(f"  {r['status'].upper():5} {r['step']}: {r.get('error') or r.get('why') or ''}")
    print(f"Report: {path}")
    return 1 if any(r["status"] in ("fail", "error") for r in lab.results) else 0


if __name__ == "__main__":
    sys.exit(main())
