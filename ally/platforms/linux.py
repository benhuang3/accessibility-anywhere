"""Linux: GNOME accessibility settings via gsettings, speech via spd-say/espeak."""

from __future__ import annotations

import os

from ..registry import ToolError
from .base import Platform, run, tempfile_path, which

MAG = "org.gnome.desktop.a11y.magnifier"
APPS = "org.gnome.desktop.a11y.applications"
A11Y_IFACE = "org.gnome.desktop.a11y.interface"
IFACE = "org.gnome.desktop.interface"


class LinuxPlatform(Platform):
    name = "linux"
    label = "Linux"

    def __init__(self):
        super().__init__()
        self._gnome: bool | None = None

    # ---- gsettings helpers
    def gnome(self) -> bool:
        if self._gnome is None:
            self._gnome = bool(which("gsettings")) and run(["gsettings", "list-keys", MAG], timeout=5).returncode == 0
        return self._gnome

    def _gset(self, schema: str, key: str, value: str) -> None:
        run(["gsettings", "set", schema, key, value], timeout=5, check=True)

    def _need_gnome(self) -> None:
        if not self.gnome():
            raise ToolError("This needs the GNOME desktop (gsettings). Other Linux desktops aren't supported yet.")

    def _magnifier_needed(self) -> bool:
        return self.magnify_level > 1.0 or self.color in ("invert", "grayscale")

    def _sync_magnifier(self) -> None:
        self._gset(MAG, "mag-factor", f"{max(1.0, self.magnify_level):.2f}")
        self._gset(APPS, "screen-magnifier-enabled", "true" if self._magnifier_needed() else "false")

    # ---- screen
    def magnify(self, level: float) -> str:
        self._need_gnome()
        level = max(1.0, min(32.0, float(level)))
        self.magnify_level = level
        if level > 1.0:
            self.last_magnify = level
        self._sync_magnifier()
        return "Screen magnifier off." if level <= 1.0 else f"Screen magnified to {level:g}x."

    def color_filter(self, kind: str) -> str:
        self._need_gnome()
        self._gset(MAG, "invert-lightness", "true" if kind == "invert" else "false")
        self._gset(MAG, "color-saturation", "0.0" if kind == "grayscale" else "1.0")
        self._gset(A11Y_IFACE, "high-contrast", "true" if kind == "high_contrast" else "false")
        self.color = kind
        self._sync_magnifier()
        return "Colors back to normal." if kind == "none" else f"Screen color filter: {kind.replace('_', ' ')}."

    def text_scale(self, factor: float) -> str:
        self._need_gnome()
        factor = max(0.5, min(3.0, float(factor)))
        self._gset(IFACE, "text-scaling-factor", f"{factor:.2f}")
        self.text_scale_factor = factor
        return f"System text size set to {round(factor * 100)}%."

    def _screenshot_fallback(self) -> bytes:
        path = tempfile_path(".png")
        try:
            for argv in (["gnome-screenshot", "-f", path], ["grim", path], ["import", "-window", "root", path],
                         ["scrot", "-o", path]):
                if which(argv[0]):
                    if run(argv, timeout=10).returncode == 0 and os.path.getsize(path) > 0:
                        with open(path, "rb") as f:
                            return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise ToolError("Can't take a screenshot. Install mss (pip install mss) or gnome-screenshot.")

    # ---- speech
    def tts_argv(self, text, rate, voice):
        if which("spd-say"):
            argv = ["spd-say", "-w", "-r", str(max(-100, min(100, int((rate - 1.0) * 100))))]
            if voice:
                argv += ["-y", voice]
            return argv + ["--", text], None
        exe = which("espeak-ng", "espeak")
        if exe:
            argv = [exe, "-s", str(int(175 * rate))]
            if voice:
                argv += ["-v", voice]
            return argv + ["--stdin"], text.encode("utf-8")
        raise ToolError("No speech program found. Install speech-dispatcher or espeak-ng.")

    def tts_cancel(self) -> None:
        if which("spd-say"):
            try:
                run(["spd-say", "-C"], timeout=3)
            except ToolError:
                pass

    def play_wav_argv(self, path: str) -> list[str]:
        exe = which("paplay", "pw-play", "aplay")
        if not exe:
            raise ToolError("No audio player found (paplay, pw-play or aplay).")
        return [exe, path]

    # ---- input / apps
    def get_selected_text(self) -> str:
        wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        options = [["wl-paste", "--primary", "--no-newline"]] if wayland else []
        options += [["xclip", "-o", "-selection", "primary"], ["xsel", "-o", "-p"]]
        for argv in options:
            if which(argv[0]):
                cp = run(argv, timeout=3)
                if cp.returncode == 0:
                    return cp.stdout.decode("utf-8", "replace").strip()
        raise ToolError("Reading selected text needs xclip, xsel or wl-clipboard installed.")

    def press_keys(self, combo: str) -> None:
        try:
            return super().press_keys(combo)
        except ToolError:
            if not which("xdotool"):
                raise
        from .. import keys

        parts = keys.split_combo(keys.expand_mod(combo))
        names = {"cmd": "super", "alt": "alt", "ctrl": "ctrl", "shift": "shift", "esc": "Escape", "enter": "Return"}
        xs = [names.get(p[1:-1], p[1:-1]) if p.startswith("<") and len(p) > 2 else p for p in parts]
        run(["xdotool", "key", "+".join(xs)], timeout=5, check=True)

    def open(self, target: str) -> None:
        exe = which("xdg-open", "gio")
        if not exe:
            raise ToolError("xdg-open is not installed.")
        argv = [exe, "open", target] if exe.endswith("gio") else [exe, target]
        run(argv, timeout=10)

    def notify(self, title: str, text: str) -> None:
        if which("notify-send"):
            try:
                run(["notify-send", title, text], timeout=5)
            except ToolError:
                pass

    def frontmost_app(self) -> str | None:
        if which("xdotool") and not os.environ.get("WAYLAND_DISPLAY"):
            cp = run(["xdotool", "getactivewindow", "getwindowname"], timeout=2)
            if cp.returncode == 0:
                return cp.stdout.decode("utf-8", "replace").strip()[:120] or None
        return None

    def checks(self):
        out = []
        session = os.environ.get("XDG_SESSION_TYPE", "unknown")
        if session == "wayland":
            out.append(("warn", "Desktop session", "Wayland: Ally's hotkey listener only sees X11 apps. Run "
                        "'python run.py gnome-shortcuts' and set hotkeys_enabled = false under [agent]"))
        else:
            out.append(("ok", "Desktop session", session))
        out.append(("ok", "GNOME settings", "found") if self.gnome() else
                   ("warn", "GNOME settings", "gsettings schemas not found: screen magnifier/filters won't work"))
        speech = which("spd-say", "espeak-ng", "espeak")
        out.append(("ok", "Speech program", speech) if speech else
                   ("warn", "Speech program", "install speech-dispatcher or espeak-ng"))
        sel = which("wl-paste", "xclip", "xsel")
        out.append(("ok", "Selection reader", sel) if sel else
                   ("warn", "Selection reader", "install xclip (X11) or wl-clipboard (Wayland)"))
        return out

    def shutdown(self) -> None:
        if self._gnome and (self.magnify_level > 1.0 or self.color != "none"):
            try:
                self.magnify_level = 1.0
                self.color_filter("none")
            except ToolError:
                pass
