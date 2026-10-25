"""Start Ally automatically when you log in.

Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run, value "Ally" (pythonw, no window)
Linux:   ~/.config/autostart/ally-agent.desktop (XDG autostart, runs in your desktop session)
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from .paths import CODE_ROOT, Paths

LABEL = "com.ally.agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Ally"


def _python(windowless: bool = False) -> str:
    exe = Path(sys.executable)
    if windowless and platform.system() == "Windows":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def windows_command(paths: Paths) -> str:
    return f'"{_python(windowless=True)}" "{CODE_ROOT / "run.py"}" start'


def _desktop_quote(arg: str) -> str:
    # Desktop Entry spec: quote args; inside quotes escape " ` $ and \ with a backslash.
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def linux_desktop_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "ally-agent.desktop"


def linux_desktop(paths: Paths) -> str:
    exec_line = " ".join(_desktop_quote(a) for a in [_python(), str(CODE_ROOT / "run.py"), "start"])
    env = f"env {_desktop_quote('ALLY_HOME=' + str(paths.home))} " if paths.home != CODE_ROOT else ""
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Ally accessibility agent\n"
            "Comment=Always-on accessibility assistant\n"
            f"Exec={env}{exec_line}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n")


def install(home=None, start_now: bool = True) -> str:
    paths = Paths(home)
    paths.ensure()
    system = platform.system()
    if system == "Windows":
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, windows_command(paths))
        msg = "Added Ally to Windows startup (HKCU Run key). It will start when you sign in."
        if start_now:
            subprocess.Popen([_python(windowless=True), str(CODE_ROOT / "run.py"), "start"], cwd=str(CODE_ROOT),
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            msg += " Started it now."
        return msg
    p = linux_desktop_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(linux_desktop(paths), encoding="utf-8")
    return f"Installed {p}. Ally will start when you log in to your desktop."


def uninstall() -> str:
    system = platform.system()
    if system == "Windows":
        import winreg  # type: ignore

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, VALUE_NAME)
            return "Removed Ally from Windows startup."
        except FileNotFoundError:
            return "Ally wasn't set to start at login."
    p = linux_desktop_path()
    if p.exists():
        p.unlink()
        return f"Removed {p}."
    return "Ally wasn't set to start at login."


def installed() -> bool:
    system = platform.system()
    if system == "Windows":
        try:
            import winreg  # type: ignore

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, VALUE_NAME)
            return True
        except OSError:
            return False
    return linux_desktop_path().exists()
