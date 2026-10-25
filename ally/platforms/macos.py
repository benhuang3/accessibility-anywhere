"""macOS: built-in Zoom and Invert Colors via their keyboard shortcuts, speech via `say`.

macOS has no public API to set the zoom level, so Ally presses Apple's shortcuts:
  Option-Command-8 (Zoom on/off), Option-Command-= (zoom in), Option-Command-- (zoom out),
  Control-Option-Command-8 (invert colors).
They need "Use keyboard shortcuts to zoom" (System Settings > Accessibility > Zoom) and
Accessibility permission for the app running Ally (System Settings > Privacy & Security).
"""

from __future__ import annotations

import ctypes
import os
import re
import time

from ..registry import ToolError
from .base import Platform, run, tempfile_path, which
from .cards import ReadingCards

KEY_EQUAL, KEY_MINUS, KEY_8, KEY_C = 24, 27, 28, 8  # ANSI virtual key codes


def osascript(script: str, timeout: float = 5) -> str:
    cp = run(["osascript", "-e", script], timeout=timeout)
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", "replace").strip()
        if "not allowed" in err or "1002" in err or "-25211" in err or "assistive" in err:
            raise ToolError("macOS blocked the key press. Give the app running Ally Accessibility permission in "
                            "System Settings > Privacy & Security > Accessibility, then try again.")
        raise ToolError(f"osascript failed: {err[:200]}")
    return cp.stdout.decode("utf-8", "replace").strip()


def key_code(code: int, *mods: str) -> None:
    using = ""
    if mods:
        using = " using {" + ", ".join(f"{m} down" for m in mods) + "}"
    osascript(f'tell application "System Events" to key code {code}{using}')


def ax_trusted() -> bool | None:
    try:
        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        fn = lib.AXIsProcessTrusted
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return None


class MacPlatform(Platform):
    name = "macos"
    label = "macOS"

    def __init__(self):
        super().__init__()
        self.cards = ReadingCards()

    def magnify(self, level: float) -> str:
        level = max(1.0, min(16.0, float(level)))
        cur = self.magnify_level
        if level <= 1.0:
            if cur > 1.0:
                key_code(KEY_8, "option", "command")  # Zoom off
            self.magnify_level = 1.0
            return "Screen zoom off."
        presses = max(1, min(12, round(abs(level - cur) * 2)))
        code = KEY_EQUAL if level > cur else KEY_MINUS
        for _ in range(presses):
            key_code(code, "option", "command")
            time.sleep(0.05)
        self.magnify_level = level
        self.last_magnify = level
        return (f"Zoomed the screen {'in' if code == KEY_EQUAL else 'out'} (about {level:g}x). If nothing changed, "
                "turn on 'Use keyboard shortcuts to zoom' in System Settings > Accessibility > Zoom.")

    def color_filter(self, kind: str) -> str:
        if kind in ("grayscale", "high_contrast"):
            raise ToolError("macOS doesn't let apps switch this on. Use System Settings > Accessibility > Display, "
                            "or press Option-Command-F5 for the Accessibility Shortcuts panel.")
        want_invert = kind == "invert"
        if want_invert != (self.color == "invert"):
            key_code(KEY_8, "control", "option", "command")
        self.color = "invert" if want_invert else "none"
        return ("Colors inverted." if want_invert else "Colors back to normal.") + \
            " (If nothing changed, turn on the Invert colors shortcut in System Settings > Keyboard > Keyboard Shortcuts > Accessibility.)"

    def _screenshot_fallback(self) -> bytes:
        path = tempfile_path(".png")
        try:
            cp = run(["screencapture", "-x", "-t", "png", path], timeout=10)
            if cp.returncode != 0 or os.path.getsize(path) == 0:
                raise ToolError("Screenshot failed. Allow Screen Recording for the app running Ally in "
                                "System Settings > Privacy & Security.")
            with open(path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def tts_argv(self, text, rate, voice):
        argv = ["say", "-r", str(max(80, min(500, int(185 * rate))))]
        if voice:
            argv += ["-v", voice]
        return argv, text.encode("utf-8")  # `say` reads stdin when no text is given

    def play_wav_argv(self, path: str) -> list[str]:
        return ["afplay", path]

    def get_selected_text(self) -> str:
        sentinel = f"__ally_{time.time_ns()}__"
        old = run(["pbpaste"], timeout=3).stdout
        run(["pbcopy"], timeout=3, input_bytes=sentinel.encode())
        try:
            key_code(KEY_C, "command")
            text = ""
            for _ in range(10):
                time.sleep(0.08)
                text = run(["pbpaste"], timeout=3).stdout.decode("utf-8", "replace")
                if text != sentinel:
                    break
        finally:
            run(["pbcopy"], timeout=3, input_bytes=old)
        return "" if text == sentinel else text.strip()

    def press_keys(self, combo: str) -> None:
        try:
            return super().press_keys(combo)
        except ToolError:
            pass
        from .. import keys

        parts = keys.split_combo(keys.expand_mod(combo))
        mods = {"cmd": "command", "ctrl": "control", "alt": "option", "shift": "shift"}
        using = [mods[p[1:-1]] for p in parts if p.startswith("<") and p[1:-1] in mods]
        normal = [p for p in parts if not (p.startswith("<") and p[1:-1] in mods)]
        if len(normal) != 1 or len(normal[0]) != 1:
            raise ToolError("Install pynput to send this key combination: pip install pynput")
        using_txt = (" using {" + ", ".join(f"{m} down" for m in using) + "}") if using else ""
        ch = normal[0].replace("\\", "\\\\").replace('"', '\\"')
        osascript(f'tell application "System Events" to keystroke "{ch}"{using_txt}')

    def open(self, target: str) -> None:
        if target.startswith("app:"):
            run(["open", "-a", target[4:]], timeout=10, check=True)
        else:
            run(["open", target], timeout=10, check=True)

    def notify(self, title: str, text: str) -> None:
        t = text.replace("\\", "\\\\").replace('"', '\\"')[:200]
        h = title.replace("\\", "\\\\").replace('"', '\\"')[:60]
        try:
            osascript(f'display notification "{t}" with title "{h}"')
        except ToolError:
            pass

    def frontmost_app(self) -> str | None:
        # lsappinfo ships with macOS and needs no Automation permission (osascript would).
        try:
            asn = run(["lsappinfo", "front"], timeout=2).stdout.decode("utf-8", "replace").strip()
            if not asn:
                return None
            out = run(["lsappinfo", "info", "-only", "name", asn], timeout=2).stdout.decode("utf-8", "replace")
        except ToolError:
            return None
        m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', out)
        return m.group(1)[:120] if m else None

    def show_text_overlay(self, box, text: str) -> str:
        return self.cards.show(box, text)

    def clear_text_overlays(self) -> None:
        self.cards.clear()

    def checks(self):
        out = []
        trusted = ax_trusted()
        if trusted is None:
            out.append(("warn", "Accessibility permission", "couldn't check"))
        elif trusted:
            out.append(("ok", "Accessibility permission", "granted"))
        else:
            out.append(("fail", "Accessibility permission",
                        "not granted: hotkeys and key presses won't work. Add your Terminal (or Python) in "
                        "System Settings > Privacy & Security > Accessibility and Input Monitoring"))
        out.append(("ok", "Speech program", "say") if which("say") else ("warn", "Speech program", "`say` missing"))
        out.append(("ok", "Zoom shortcuts", "turn on 'Use keyboard shortcuts to zoom' in System Settings > "
                    "Accessibility > Zoom so magnify_screen works"))
        return out

    def shutdown(self) -> None:
        self.cards.close()
        try:
            if self.magnify_level > 1.0:
                self.magnify(1.0)
            if self.color == "invert":
                self.color_filter("none")
        except ToolError:
            pass
