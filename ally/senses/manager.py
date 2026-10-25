"""Senses in the agent: face signals (from the shared camera helper) and mouse/keyboard signals (from
ally.senses.input_helper) -> detectors -> fusion -> Struggle events for the brain.

The webcam is opened only by ally.gaze_helper, which also runs the eye tracker, so the two never
fight over the camera. With [senses] vision on, that helper sends a few face numbers per frame;
frames are never saved or sent anywhere.

Delivery: every Struggle goes to the event log (kind "struggle"), to callbacks registered with
subscribe(), and to agent.on_struggle(dict) when the agent defines it (the brain's entry point).
"""

from __future__ import annotations

import collections
import json
import logging
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from ..hotkeys import python_for_children
from ..paths import CODE_ROOT
from ..platforms.base import NO_WINDOW
from ..registry import ToolError
from ..state import atomic_write
from .detectors import (Baseline, HesitationDetector, LeanDetector, RageClickDetector, RetypeDetector, Signal,
                        SquintDetector, Tuning, gate, RelativeSquintDetector, GeometricSquintDetector,
                        PersonalSquintDetector, HybridSquintDetector, DistanceLeanDetector, HybridLeanDetector)
from .fusion import Fuser, Struggle
from .contract import validate_struggle
from .features import extract as extract_face_features

log = logging.getLogger("ally.senses")

AUTO_BASELINE_SECONDS = 20  # with no saved baseline, learn one quietly from the first 20 s of face
SIMULATED = {"squint", "lean", "rage_click", "hesitation", "retype"}


class Senses:
    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.RLock()
        self.vision_override: bool | None = None  # "stop watching me" lasts until Ally restarts
        self.input_override: bool | None = None
        self.baseline = self._load_baseline()
        self.tune = Tuning()
        self.fuser = Fuser()
        self._build()
        self.face: dict | None = None
        self.face_at = 0.0
        self._face_t = 0.0
        self._auto: list[dict] = []
        self._calib: list[dict] | None = None
        self._calib_until = 0.0
        self._calib_parts: dict[str, list[dict]] = {}
        self._calib_phase = ""
        self._calib_mode = False
        self._calib_done = threading.Event()
        self._calib_result: dict | None = None
        self._last_struggle_t = -1e9
        self._demo_due: float | None = None
        self._simulated_until = 0.0
        self.pointer: tuple[int, int] | None = None
        self.counts: collections.Counter = collections.Counter()
        self.last_signal: dict | None = None
        self.last_struggle: dict | None = None
        self.snoozed_until: dict[str, float] = {}
        self.subscribers: list = []
        self.hud = False
        self._hud_at = 0.0
        self.input = InputWatcher(self)
        if self.cfg.get("post_url"):
            from ..struggle_sink import StruggleSink
            self.subscribe(StruggleSink(self.agent, self.cfg["post_url"]))

    # ---------------------------------------------------------------- settings
    @property
    def cfg(self) -> dict:
        return self.agent.config.data.get("senses", {})

    def vision_wanted(self) -> bool:
        return self.vision_override if self.vision_override is not None else bool(self.cfg.get("vision"))

    def input_wanted(self) -> bool:
        return self.input_override if self.input_override is not None else bool(self.cfg.get("input"))

    def _build(self) -> None:
        cfg = self.cfg
        self.tune = Tuning.from_config(cfg)
        self._methods = (cfg.get("squint_method", "v1"), cfg.get("lean_method", "v1"))
        self.squint = {"relative": RelativeSquintDetector, "geometric": GeometricSquintDetector,
                       "personal": PersonalSquintDetector, "hybrid": HybridSquintDetector}.get(
                           cfg.get("squint_method"), SquintDetector
                       )(self.tune)
        self.lean = {"distance": DistanceLeanDetector, "hybrid": HybridLeanDetector}.get(
            cfg.get("lean_method"), LeanDetector)(self.tune)
        self.rage, self.retype = RageClickDetector(self.tune), RetypeDetector(self.tune)
        self.hesitation = HesitationDetector(self.tune)
        self.fuser.configure(float(cfg.get("window_seconds", 4)), float(cfg.get("cooldown_seconds", 30)),
                             cfg.get("solo") or [], float(cfg.get("min_confidence", 0.5)))

    def apply(self) -> None:
        """Match config: rebuild detectors with new thresholds and start or stop the input watcher."""
        with self.lock:
            if Tuning.from_config(self.cfg) != self.tune or self._methods != (
                    self.cfg.get("squint_method", "v1"), self.cfg.get("lean_method", "v1")):
                self._build()
            else:
                self.fuser.configure(float(self.cfg.get("window_seconds", 4)),
                                     float(self.cfg.get("cooldown_seconds", 30)), self.cfg.get("solo") or [],
                                     float(self.cfg.get("min_confidence", 0.5)))
        self.input.apply()

    def set_vision(self, on: bool) -> None:
        self.vision_override = bool(on)
        if not on:
            self.face = None
        gaze = getattr(self.agent, "gaze", None)
        if gaze is not None:
            gaze.apply()

    def set_input(self, on: bool) -> None:
        self.input_override = bool(on)
        self.input.apply()

    def stop(self) -> None:
        self.input.stop()

    def subscribe(self, callback) -> None:
        """callback(struggle_dict) is called on a worker thread for every Struggle."""
        self.subscribers.append(callback)

    def answer(self, struggle: dict, answer: str) -> None:
        if answer not in ("yes", "no"):
            raise ValueError("answer must be yes or no")
        if answer == "no":
            now = float(struggle.get("ts", time.time()))
            kinds = {p.get("kind") for p in struggle.get("detail", {}).get("signals", [])}
            kinds.add(struggle.get("kind"))
            until = now + float(self.cfg.get("decline_seconds", 90))
            with self.lock:
                for kind in kinds:
                    if kind:
                        self.snoozed_until[kind] = until

    # ---------------------------------------------------------------- baseline
    @property
    def baseline_path(self) -> Path:
        return self.agent.paths.state_dir / "senses_baseline.json"

    def _load_baseline(self) -> Baseline | None:
        try:
            return Baseline.from_dict(json.loads(self.baseline_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, AttributeError):
            return None

    def _save_baseline(self) -> None:
        if self.baseline is None:
            return
        try:
            atomic_write(self.baseline_path, json.dumps(self.baseline.to_dict()))
        except OSError as e:
            log.warning("couldn't save the face baseline: %s", e)

    def calibrate(self, seconds: float | None = None) -> dict:
        """Collect labelled reading and expression samples for a personal baseline."""
        requested = float(seconds or self.cfg.get("calibrate_seconds", 35))
        if not self.vision_wanted():
            self.set_vision(True)
        end = time.monotonic() + 20
        while time.time() - self.face_at > 1.0:
            gaze = getattr(self.agent, "gaze", None)
            if time.monotonic() > end or (gaze is not None and gaze.error and not gaze.running):
                why = (gaze.error if gaze is not None and gaze.error else "I can't see your face. "
                       "Face the screen and the camera, with light on your face.")
                raise ToolError(why)
            time.sleep(0.2)
        gaze = getattr(self.agent, "gaze", None)
        if seconds is not None and requested <= 5:
            return self._calibrate_quick(requested, gaze)
        phases = [
            ("neutral", 15, "Read normally with the card in the middle.", "middle"),
            ("top", 5, "Read while looking at the top of the screen.", "top"),
            ("bottom", 5, "Read while looking at the bottom of the screen.", "bottom"),
            ("smile", 3, "Smile naturally while you read.", "middle"),
            ("squint", 3, "Squint at the small text.", "middle"),
            ("rest", 2, "Relax your eyes for a moment.", "middle"),
            ("squint", 3, "Squint at the small text once more.", "middle"),
        ]
        if requested != 35:
            scale = requested / 36.0
            phases = [(name, duration * scale, text, place) for name, duration, text, place in phases]
        with self.lock:
            self._calib_parts = {}
            self._calib_result = None
            self._calib_mode = True
        try:
            for name, duration, prompt, place in phases:
                phase_name = name[0] if isinstance(name, tuple) else name
                if phase_name == "rest":
                    time.sleep(duration)
                    continue
                self._begin_calibration_phase(phase_name, duration)
                if gaze is not None:
                    gaze.send(f"card place {place}")
                    gaze.send("card " + json.dumps({
                        "title": "Face setup",
                        "text": prompt,
                        "size": "normal",
                        "seconds": duration,
                    }))
                if not self._calib_done.wait(duration + 10):
                    raise ToolError("I lost sight of your face during setup. Try again facing the screen.")
            return self._finish_personal_calibration()
        finally:
            with self.lock:
                self._calib = None
                self._calib_phase = ""
                self._calib_mode = False
            if gaze is not None:
                gaze.send("card off")

    def _calibrate_quick(self, seconds: float, gaze) -> dict:
        """Keep the short explicit calibration hook used by tests and scripted setup."""
        with self.lock:
            self._calib_done.clear()
            self._calib_result = None
            self._calib_phase = "neutral"
            self._calib_mode = False
            self._calib = []
            self._calib_until = self._face_t + seconds
        if gaze is not None:
            gaze.send("card " + json.dumps({"title": "Face setup", "text": "Read normally.",
                                             "size": "normal", "seconds": seconds}))
        try:
            if not self._calib_done.wait(seconds + 10):
                raise ToolError("I lost sight of your face during setup. Try again facing the screen.")
        finally:
            if gaze is not None:
                gaze.send("card off")
        return self._calib_result or {"ok": False, "why": "the camera stopped"}

    def _begin_calibration_phase(self, name: str, seconds: float) -> None:
        with self.lock:
            self._calib_done.clear()
            self._calib_phase = name
            self._calib = []
            self._calib_until = self._face_t + seconds

    def _finish_personal_calibration(self) -> dict:
        with self.lock:
            parts = dict(self._calib_parts)
        neutral = parts.get("neutral", [])
        try:
            self.baseline = Baseline.from_samples(neutral, time.time(), "calibrated")
        except ValueError as error:
            return {"ok": False, "why": str(error)}
        model_parts = {
            "neutral": parts.get("neutral", []),
            "top": parts.get("top", []),
            "bottom": parts.get("bottom", []),
            "smile": parts.get("smile", []),
            "squint": parts.get("squint", []),
        }
        model = PersonalSquintDetector.fit(model_parts)
        if self.baseline.feature_stats is None:
            self.baseline.feature_stats = {}
        self.baseline.feature_stats["personal_model"] = model
        self._save_baseline()
        self._reset_vision()
        message = "Face baseline set."
        if not model.get("enabled"):
            message = "Squint sensing is off for you because the deliberate squint was not distinct. Lean and other signals still work."
        self._calib_result = {"ok": True, "baseline": self.baseline.to_dict(),
                              "squint_enabled": bool(model.get("enabled")), "why": model.get("why", "")}
        self.agent.events.add("senses", message)
        return self._calib_result

    def _finish_calibration(self, t: float) -> None:
        samples, self._calib = self._calib or [], None
        if self._calib_mode:
            self._calib_parts.setdefault(self._calib_phase, []).extend(samples)
            self._calib_phase = ""
            self._calib_done.set()
            return
        if self._calib_phase and self._calib_phase != "neutral":
            self._calib_parts.setdefault(self._calib_phase, []).extend(samples)
            self._calib_phase = ""
            self._calib_done.set()
            return
        try:
            self.baseline = Baseline.from_samples(samples, t, "calibrated")
            self._save_baseline()
            self._reset_vision()
            self._calib_result = {"ok": True, "baseline": self.baseline.to_dict()}
            self.agent.events.add("senses", f"Face baseline set from {self.baseline.samples} frames "
                                            f"(squint {self.baseline.squint:.2f}, width {self.baseline.width:.3f}).")
        except ValueError as e:
            self._calib_result = {"ok": False, "why": str(e)}
        self._calib_done.set()

    def _reset_vision(self) -> None:
        self._build()

    def forget_baseline(self) -> None:
        with self.lock:
            self.baseline, self._auto = None, []
            try:
                self.baseline_path.unlink()
            except OSError:
                pass

    # ---------------------------------------------------------------- face
    def on_face(self, t: float, sig: dict) -> None:
        """One frame's face numbers from the camera helper (on its reader thread)."""
        if not self.vision_wanted():
            return
        if isinstance(sig.get("lm"), list) and len(sig["lm"]) >= 478:
            try:
                sig = {**sig, **extract_face_features(sig["lm"], sig.get("bs"), sig.get("mat"),
                                                     sig.get("w", 1), sig.get("h", 1))}
            except (IndexError, TypeError, ValueError):
                pass
        fired = []
        with self.lock:
            dt = t - self._face_t if self._face_t else 0.0
            self._face_t = t
            self.face, self.face_at = sig, time.time()
            if self._calib is not None:
                self._calib.append(sig)
                if t >= self._calib_until:
                    self._finish_calibration(t)
            elif self.baseline is None:
                self._auto.append(sig)
                fps = max(1, int(self.cfg.get("fps", 10)))
                if len(self._auto) >= AUTO_BASELINE_SECONDS * fps:
                    try:
                        self.baseline = Baseline.from_samples(self._auto, t, "auto")
                        self._save_baseline()
                        self.agent.events.add("senses", "Learned your resting face from the first 20 seconds. "
                                                        "Say 'set up face sensing' for a better baseline.")
                    except ValueError:
                        del self._auto[: len(self._auto) // 2]  # keep trying with fresher frames
            else:
                squint_signal = self.squint.feed(sig, t, self.baseline) if self.baseline.squint_reliable else None
                for s in (squint_signal, self.lean.feed(sig, t, self.baseline)):
                    if s is not None:
                        fired.append(s)
                # A lean or squint that is still going on stays eligible for pairing: people keep leaning
                # in while they start to squint, often more than window_seconds after the lean fired.
                if self.lean.fired and self.lean.ratio >= self.tune.lean_weak:
                    self.fuser.hold("lean", t)
                if self.squint.latched and not self.squint.gated and self.squint.delta >= self.tune.squint_weak:
                    self.fuser.hold("squint", t)
                squint_near = (sig["squint_l"] + sig["squint_r"]) / 2 - self.baseline.squint >= self.tune.squint_weak
                lean_near = sig["width"] / self.baseline.width >= self.tune.lean_weak
                cooldown = t < self.fuser.last_emit + self.fuser.cooldown
                snoozed = any(until > t for until in self.snoozed_until.values())
                recent_struggle = t - self._last_struggle_t < self.fuser.window
                if (not fired and not squint_near and not lean_near and not cooldown and not snoozed
                        and not recent_struggle
                        and not gate(sig, self.tune, wink=self.baseline.wink(sig))
                        and not self.squint.latched and not self.lean.fired):
                    self.baseline.drift(sig, dt)
            hud = self.hud and time.time() - self._hud_at > 0.2
            if hud:
                self._hud_at = time.time()
        for s in fired:
            self._signal(s)
        if hud:
            self._push_hud()

    # ---------------------------------------------------------------- mouse and keyboard
    def on_input(self, msg: dict) -> None:
        e, t = msg.get("e"), float(msg.get("t") or time.time())
        sig = None
        with self.lock:
            if e in ("c", "m"):
                x, y = int(msg.get("x", 0)), int(msg.get("y", 0))
                self.pointer = (x, y)
                if e == "c":
                    self.hesitation.click(t, x, y)
                    sig = self.rage.click(t, x, y)
                    self._forward_click(t, x, y)
                else:
                    self.hesitation.move(t, x, y)
            elif e == "r":
                self.hesitation.clickable = msg.get("clickable") if msg.get("clickable") in (True, False) else None
            elif e in ("k", "b"):
                self.hesitation.key(t)
                sig = self.retype.key(t, e == "b")
        if sig is not None:
            self._signal(sig)

    def _forward_click(self, t: float, x: int, y: int) -> None:
        """The eye tracker learns from clicks. Nothing about the click is sent off this computer."""
        gaze = getattr(self.agent, "gaze", None)
        if gaze is None or not hasattr(gaze, "on_click"):
            return
        try:
            gaze.on_click(t, x, y)
        except Exception:
            log.debug("couldn't forward a click to the eye tracker", exc_info=True)

    def tick(self, t: float | None = None) -> None:
        """Called about once a second (hesitation needs a clock, not just events)."""
        replay = t is not None
        t = time.time() if t is None else float(t)
        with self.lock:
            sig = self.hesitation.tick(t) if self.input.running or replay else None
            demo_due = self._demo_due
            if demo_due is not None and t >= demo_due:
                self._demo_due = None
            else:
                demo_due = None
        if demo_due is not None:
            self._signal(Signal("lean", 0.4, demo_due, "weak", "simulate", detail={"simulated": True}))
        if sig is not None:
            self._signal(sig)

    # ---------------------------------------------------------------- signals -> struggles
    def simulate(self, kind: str, confidence: float | None = None, tier: str = "strong") -> dict | None:
        """Inject a signal as if a detector fired (demo fallback). "demo" = a weak squint plus a weak lean."""
        now = time.time()
        with self.lock:
            self.fuser.last_emit = -1e9  # a simulation never waits for the cooldown
        if kind == "demo":
            self._demo_due = now + 1.5
            self._simulated_until = now + 3.0
            self._signal(Signal("squint", 0.4, now, "weak", "simulate", detail={"simulated": True}))
            return None
        if kind not in SIMULATED:
            raise ToolError(f"Unknown signal {kind!r}. Use one of: demo, {', '.join(sorted(SIMULATED))}.")
        conf = confidence if confidence is not None else (0.7 if tier == "strong" else 0.4)
        x, y = self.pointer or (None, None)
        return self._signal(Signal(kind, float(conf), now, tier, "simulate", x, y, {"simulated": True}))

    def _signal(self, sig: Signal) -> dict | None:
        with self.lock:
            if self.snoozed_until.get(sig.kind, -1) > sig.ts:
                return None
            self.counts[sig.kind] += 1
            self.last_signal = {"kind": sig.kind, "tier": sig.tier, "confidence": sig.confidence, "ts": sig.ts}
            struggle = self.fuser.add(sig)
        log.info("signal: %s %s %.2f %s", sig.tier, sig.kind, sig.confidence, sig.detail)
        self.agent.events.add("sense", f"{sig.tier} {sig.kind} ({sig.confidence:.2f})", source=sig.source)
        if struggle is None:
            return None
        return self._emit(struggle)

    def target(self, struggle: Struggle) -> dict:
        """Where it happened: the app in front, the website (if Chrome is in front), the pointer.

        Unknown text is "" (ally.contracts.Target turns None into the text "None", which made the
        coordinator treat every struggle as a web page). "app" is the program's name and "window"
        its title, split as Agent.proactive_context does, so habits are kept per app, not per window.
        """
        target = {"app": "", "site": "", "window": "", "x": None, "y": None, "box": None}
        try:
            title = self.agent.platform.frontmost_app() or ""
        except Exception:
            title = ""
        target["app"] = title.rsplit(" - ", 1)[-1] if " - " in title else title  # "Inbox - Google Chrome"
        target["window"] = title
        bridge = getattr(self.agent, "bridge", None)
        try:
            if bridge is not None and bridge.chrome_focused:
                url = str(bridge.tab.get("url") or "")
                target["site"] = urllib.parse.urlsplit(url).hostname or ""
                target["window"] = bridge.tab.get("title") or title
        except Exception:
            pass
        x, y = struggle.target.get("x"), struggle.target.get("y")
        if x is None:  # face signals: where the eyes are, when the eye tracker is sure; else the pointer
            gaze = getattr(self.agent, "gaze", None)
            try:
                seen = gaze.target() if gaze is not None and hasattr(gaze, "target") else None
            except Exception:
                seen = None
            if seen and seen.get("source") == "eyes":
                x, y = seen["x"], seen["y"]
        if x is None and self.pointer:
            x, y = self.pointer
        target["x"], target["y"] = x, y
        return target

    def _emit(self, struggle: Struggle) -> dict:
        struggle.target = self.target(struggle)
        d = struggle.to_dict()
        problems = validate_struggle(d)
        if problems:
            log.error("dropping invalid struggle: %s", "; ".join(problems))
            return None
        with self.lock:
            self.last_struggle = d
            self._last_struggle_t = struggle.ts
        log.info("%s", struggle.describe())
        self.agent.events.add("struggle", struggle.describe(), struggle=d)
        handlers = list(self.subscribers)
        hook = getattr(self.agent, "on_struggle", None)
        if callable(hook):
            handlers.append(hook)
        for h in handlers:  # the brain may wait for a yes/no; never block the camera or input threads
            threading.Thread(target=self._deliver, args=(h, d), daemon=True, name="struggle").start()
        return d

    @staticmethod
    def _deliver(handler, d: dict) -> None:
        try:
            handler(dict(d))
        except Exception:
            log.exception("struggle handler failed")

    # ---------------------------------------------------------------- status and HUD
    def vision_status(self) -> str:
        if not self.vision_wanted():
            return "off"
        gaze = getattr(self.agent, "gaze", None)
        if gaze is not None and gaze.error and not gaze.ready:
            return "error"
        if time.time() - self.face_at > 2.0:
            return "starting" if gaze is not None and not gaze.ready else "can't see you"
        if self._calib is not None:
            return "calibrating"
        if self.baseline is None:
            return "learning your face"
        if not self.baseline.squint_reliable:
            return "watching (squint off: " + self.baseline.squint_why + ")"
        return "watching"

    def info(self) -> dict:
        with self.lock:
            face = dict(self.face) if self.face and time.time() - self.face_at < 2 else None
            out = {
                "vision": self.vision_status(), "input": self.input.status(), "error": self.input.error,
                "baseline": self.baseline.to_dict() if self.baseline else None,
                "face": face, "gate": self.squint.gated if face else "",
                "squint_delta": round(self.squint.delta, 3), "lean_ratio": round(self.lean.ratio, 3),
                "hesitation_threshold": round(self.hesitation.threshold(), 1),
                "signals": dict(self.counts), "last_signal": self.last_signal, "last_struggle": self.last_struggle,
                "snoozed_until": dict(self.snoozed_until),
                "simulated": self._simulated_until > time.time(),
            }
        gaze = getattr(self.agent, "gaze", None)
        if out["vision"] == "error" and gaze is not None:
            out["error"] = gaze.error
        return out

    def hud_lines(self) -> list[list[str]]:
        """Rows for the debug window: [text, "ok" | "warn" | "hot"]."""
        i = self.info()
        f, b, tune = i["face"] or {}, i["baseline"] or {}, self.tune
        rows = [[f"vision: {i['vision']}   input: {i['input']}", "ok"]]
        if i.get("simulated"):
            rows.append(["SIMULATED struggle demo", "warn"])
        if b and not b.get("squint_reliable", True):
            rows.append(["squint off: " + b.get("squint_why", "unreliable calibration"), "warn"])
        active_snoozes = [f"{k} {max(0, v - time.time()):.0f}s" for k, v in i.get("snoozed_until", {}).items()
                          if v > time.time()]
        if active_snoozes:
            rows.append(["declined: " + ", ".join(active_snoozes), "warn"])
        if f:
            s = (f["squint_l"] + f["squint_r"]) / 2
            rows.append([f"squint {s:.2f} (L {f['squint_l']:.2f} R {f['squint_r']:.2f})  base "
                         f"{b.get('squint', 0):.2f}  delta {i['squint_delta']:+.2f} / {tune.squint_delta:.2f}",
                         "hot" if i["squint_delta"] >= tune.squint_delta else
                         ("warn" if i["squint_delta"] >= tune.squint_weak else "ok")])
            rows.append([f"lean x{i['lean_ratio']:.2f} / {tune.lean_ratio:.2f}  (width {f['width']:.3f})",
                         "hot" if i["lean_ratio"] >= tune.lean_ratio else
                         ("warn" if i["lean_ratio"] >= tune.lean_weak else "ok")])
            rows.append([f"smile {f['smile']:.2f}  blink {f['blink']:.2f}  yaw {f['yaw']:+.0f}  pitch "
                         f"{f['pitch']:+.0f}  light {f['light']:.0f}", "ok"])
            rows.append([f"gate: {i['gate'] or 'none'}", "warn" if i["gate"] else "ok"])
        counts = ", ".join(f"{k} {v}" for k, v in sorted(i["signals"].items())) or "none yet"
        rows.append([f"signals: {counts}", "ok"])
        if i["last_struggle"]:
            ls = i["last_struggle"]
            ago = time.time() - ls["ts"]
            rows.append([f"last struggle: {ls['kind']} {ls['confidence']:.2f} ({ago:.0f} s ago)",
                         "hot" if ago < 5 else "ok"])
        return rows

    def set_hud(self, on: bool) -> None:
        self.hud = bool(on)
        gaze = getattr(self.agent, "gaze", None)
        if gaze is not None:
            gaze.send("hud on" if on else "hud off")

    def _push_hud(self) -> None:
        gaze = getattr(self.agent, "gaze", None)
        if gaze is not None:
            gaze.send("hud " + json.dumps(self.hud_lines()))


class InputWatcher:
    """Runs ally.senses.input_helper and restarts it if it dies (with limits), like the hotkey helper."""

    def __init__(self, senses: Senses):
        self.senses = senses
        self.proc: subprocess.Popen | None = None
        self.ready = False
        self.error = ""
        self._crashes: list[float] = []
        self._gave_up = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> str:
        if not self.senses.input_wanted():
            return "off"
        if self.error and not self.ready:
            return "error"
        return "watching" if self.ready and self.running else "starting"

    def apply(self) -> None:
        if not self.senses.input_wanted():
            self.stop()
            return
        if self.running or self._gave_up:
            return
        self._gave_up, self._crashes = False, []
        self._start()

    def _start(self) -> None:
        with self._lock:
            self._kill()
            self.error = ""
            try:
                self.proc = subprocess.Popen(
                    [python_for_children(), "-m", "ally.senses.input_helper"], cwd=str(CODE_ROOT),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                    encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            except OSError as e:
                self.error = f"couldn't start the mouse and keyboard watcher: {e}"
                return
            proc = self.proc
        threading.Thread(target=self._read, args=(proc,), daemon=True, name="senses-input").start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "e" in msg:
                try:
                    self.senses.on_input(msg)
                except Exception:
                    log.exception("input signal failed")
            elif msg.get("ready"):
                self.ready, self.error = True, ""
                log.info("mouse and keyboard signals on")
            elif "error" in msg:
                self.error = str(msg["error"])[:300]
                log.warning("input watcher: %s", self.error)
        self.ready = False
        try:
            proc.stdout.close()
        except OSError:
            pass

    def ensure_running(self) -> None:
        if not self.senses.input_wanted() or self._gave_up:
            return
        proc = self.proc
        if proc is None or proc.poll() is None:
            return
        if proc.returncode in (3, 4):  # pynput missing or no display: restarting won't help
            self._gave_up = True
            return
        now = time.time()
        self._crashes = [t for t in self._crashes if now - t < 120] + [now]
        if len(self._crashes) > 4:
            self._gave_up = True
            self.error = self.error or "the mouse and keyboard watcher keeps stopping"
            return
        log.warning("input watcher exited (%s); restarting", proc.returncode)
        self._start()

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        self.ready = False
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()

    def stop(self) -> None:
        with self._lock:
            self._kill()
        self._gave_up = False
