"""Command line: python run.py [start|agent|doctor|install|uninstall|send|tool|status|stop|restart|open|check]"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import webbrowser

from .paths import Paths, load_env_file

HELP = """
commands:
  start              run Ally with auto-restart (default)
  agent              run the agent only, no auto-restart (for development)
  doctor             check setup and print fixes
  install            start Ally automatically at login
  uninstall          stop starting Ally at login
  send "text"        send a request, e.g. python run.py send "make it bigger"
  tool NAME [JSON]   run one tool, e.g. python run.py tool set_page_zoom '{"zoom": 1.5}'
  status             show what Ally is doing
  stop | restart     stop or restart the running agent
  open               open the dashboard
  check              validate config.toml without starting
  say "text"         speak with Ally's voice (Cartesia, or the built-in voice), e.g. python run.py say "Hello"
  ask "question"     ask a spoken yes/no question and print the answer (yes / no / timeout)
  answer yes|no      answer the open spoken question (what the on-screen buttons do)
  warn KIND          spoken risk warning: scam_popup, untrusted_site, remote_access, payment_field
  voices [NAME]      list Cartesia voices (for speech.cartesia_voice_id)
  voice-test [VOICE...]  render Ally's prompts with each voice; prints time to audio and words per minute
                     (add --play to hear them)
  gnome-shortcuts    Linux/GNOME: register [hotkeys] as GNOME shortcuts (works on Wayland)
  gnome-shortcuts-remove   remove those GNOME shortcuts
"""


def _api(paths: Paths, method: str, path: str, body: dict | None = None, timeout: float = 60):
    from .supervisor import current_port, listening

    port = current_port(paths)
    try:
        token = paths.token_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise SystemExit("Ally hasn't run yet (no token). Start it with: python run.py")
    if not listening(port):
        raise SystemExit(f"Ally isn't running on port {port}. Start it with: python run.py")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"X-Ally-Token": token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except ValueError:
            raise SystemExit(f"Ally answered with error {e.code}")
    except (urllib.error.URLError, OSError):
        raise SystemExit(f"Ally isn't running on port {port}. Start it with: python run.py")


def _agent_running(paths: Paths) -> bool:
    from .supervisor import current_port, listening

    return paths.token_file.exists() and listening(current_port(paths))


def _voice_command(paths: Paths, cmd: str, a) -> int:
    """Voice commands. say / ask / warn go through the running agent when there is one (so they
    share its microphone and settings), and otherwise run here on their own."""
    text = " ".join(a.args).strip()
    if cmd == "voices":
        from .speech import CartesiaError, cartesia_voices

        try:
            voices = cartesia_voices(q=text)
        except CartesiaError as e:
            print(e)
            return 1
        for v in voices:
            print(f"{v.get('name') or '?':32} {v.get('id')}  {v.get('gender') or ''} {v.get('language') or ''}")
        print(f"{len(voices)} voice(s). Put a name or ID in config.toml: [speech] cartesia_voice_id = \"...\"")
        return 0
    if cmd == "voice-test":
        from .platforms import get_platform
        from .voice_confirm import audition

        rows = audition(a.args or ["Tessa", "Leo", "Marian", "Dana"], play=a.play, platform=get_platform())
        for r in rows:
            if r.get("error"):
                print(f"{r['voice']:10} ERROR {r['error']}")
            else:
                print(f"{r['voice']:10} {r['request_ms']:5} ms  {r['seconds']:5.2f} s  {r['wpm']:6.1f} wpm  {r['text']}")
        return 0 if rows and not all(r.get("error") for r in rows) else 1
    if cmd == "answer":
        if text not in ("yes", "no"):
            print("Usage: python run.py answer yes   (or no)")
            return 2
        out = _api(paths, "POST", "/api/voice/answer", {"answer": text, "by": "cli"})
        print("Answered." if out.get("answered") else "No spoken question is open.")
        return 0
    if _agent_running(paths):
        if cmd == "say":
            out = _api(paths, "POST", "/api/voice/say", {"text": text or "Hello.", "style": a.style or "calm",
                                                        "wait": True}, timeout=120)
            print(f"spoke with: {out.get('engine') or '?'}")
            return 0
        if cmd == "warn":
            out = _api(paths, "POST", "/api/voice/warn", {"kind": text or "scam_popup", "severity": "high"},
                       timeout=120)
        else:
            out = _api(paths, "POST", "/api/voice/ask", {"prompt": text or "Want me to make this bigger?",
                                                        "kind": "question"}, timeout=120)
        print(json.dumps(out, indent=2))
        return 0
    from .voice_confirm import standalone

    vc = standalone(a.home)
    if cmd == "say":
        vc.say(text or "Hello.", a.style or "calm", wait=True)
        info = vc.speaker.info()
        print(f"spoke with: {info['last_engine'] or '?'}" + (f" ({info['fallback_reason']})" if
                                                           info["fallback_reason"] else ""))
        return 0
    if cmd == "warn":
        ans = vc.warn({"kind": text or "scam_popup", "severity": "high"})
    else:
        ans = vc.ask(text or "Want me to make this bigger?", kind="question")
    print(json.dumps(ans.to_dict() if ans is not None else {"value": "said"}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        print("Ally needs Python 3.11 or newer.")
        return 1
    p = argparse.ArgumentParser(prog="run.py", description="Ally, an always-on accessibility agent.",
                                epilog=HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", default="start")
    p.add_argument("args", nargs="*")
    p.add_argument("--home", help="folder for config, logs and state (default: this folder)")
    p.add_argument("--no-hotkeys", action="store_true", help="agent: don't start global hotkeys")
    p.add_argument("--style", default="", help="say: calm, offer, done, sorry, warning, read")
    p.add_argument("--play", action="store_true", help="voice-test: play each sample")
    a = p.parse_args(argv)
    paths = Paths(a.home)
    load_env_file(paths.env_file)
    cmd = a.command

    if cmd == "start":
        from .supervisor import supervise

        return supervise(a.home)
    if cmd == "agent":
        from .agent import Agent

        return Agent(a.home, start_hotkeys=not a.no_hotkeys).run_forever()
    if cmd == "doctor":
        from .doctor import main as doctor

        return doctor(a.home)
    if cmd == "install":
        from .autostart import install

        print(install(a.home))
        return 0
    if cmd == "uninstall":
        from .autostart import uninstall

        print(uninstall())
        return 0
    if cmd == "check":
        from .config import ConfigManager

        cm = ConfigManager(paths.config)
        _cfg, errors, warnings = cm.check_text(cm.read_text())
        for w in warnings:
            print("warning:", w)
        for e in errors:
            print("error:", e)
        print("config.toml is valid." if not errors else f"{len(errors)} error(s).")
        return 1 if errors else 0
    if cmd == "send":
        text = " ".join(a.args).strip()
        if not text:
            print('Usage: python run.py send "make the text bigger"')
            return 2
        out = _api(paths, "POST", "/api/command", {"text": text, "source": "cli"})
        print(out.get("text") or out.get("error") or out)
        return 0
    if cmd == "tool":
        if not a.args:
            print('Usage: python run.py tool NAME \'{"arg": value}\'')
            return 2
        args = json.loads(a.args[1]) if len(a.args) > 1 else {}
        out = _api(paths, "POST", "/api/tool", {"name": a.args[0], "args": args, "speak": True})
        print(out.get("text") or out.get("error"))
        return 0 if out.get("ok") else 1
    if cmd in ("say", "ask", "answer", "warn", "voices", "voice-test"):
        return _voice_command(paths, cmd, a)
    if cmd == "status":
        print(json.dumps(_api(paths, "GET", "/api/status", timeout=10), indent=2))
        return 0
    if cmd in ("stop", "restart"):
        _api(paths, "POST", f"/api/{'shutdown' if cmd == 'stop' else 'restart'}", {}, timeout=10)
        print("Stopping Ally." if cmd == "stop" else "Restarting Ally.")
        return 0
    if cmd == "open":
        from .supervisor import current_port

        webbrowser.open(f"http://127.0.0.1:{current_port(paths)}/")
        return 0
    if cmd in ("gnome-shortcuts", "gnome-shortcuts-remove"):
        from . import gnome_shortcuts
        from .config import ConfigManager
        from .registry import PluginLoader, Registry
        from .tools import BUILTIN_MODULES

        try:
            if cmd == "gnome-shortcuts-remove":
                print(f"Removed {gnome_shortcuts.remove()} Ally shortcut(s).")
                return 0
            reg = Registry()
            for m in BUILTIN_MODULES:
                reg.load_module(m)
            cm = ConfigManager(paths.config)
            cm.reload(reg.names())
            PluginLoader(reg, paths.plugins_dir(cm.data["plugins"]["folder"])).scan()
            names = reg.names() | set(cm.data.get("commands") or {})
            for line in gnome_shortcuts.install(cm.data, paths, names):
                print("  " + line)
            print("Done. Set hotkeys_enabled = false under [agent] in config.toml so keys don't fire twice.")
            return 0
        except (RuntimeError, OSError, ValueError) as e:
            print(f"Couldn't change GNOME shortcuts: {e}")
            return 1
    p.print_help()
    return 2
