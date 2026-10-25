import json
import shutil
import subprocess
import threading
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

from helpers import ROOT

from ally.config import DEFAULTS, merge, validate
from ally.contracts import EventBus, Risk
from ally.risk import contact
from ally.risk.wire import RiskWire, _make_contact, attach

BASE = "https://ganeshkar1.github.io/ally-call/"
MEET = "https://meet.google.com/abc-defg-hij"
RISK = Risk("scam_popup", "high", "A fake warning page.")


def fragment(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).fragment))


class SpacecallTests(unittest.TestCase):
    def make_wire(self, settings=None, send=None, **kwargs):
        cfg = {"trusted_contact": "Priya", "contact_topic": "ally-priya-7f3k29qx",
               "call_service": "spacecall", "spacecall_url": BASE}
        cfg.update(settings or {})
        self.sent, self.opened, self.events = [], [], []
        self.delivered = threading.Event()
        sender = send or (lambda *args: self.sent.append(args) or True)
        agent = SimpleNamespace(config=SimpleNamespace(data={"risk": cfg}))
        with patch.object(contact, "ntfy_sender", return_value=sender) as factory:
            trusted = _make_contact(agent)
        expected_base = cfg["spacecall_url"] if contact.valid_spacecall_url(cfg["spacecall_url"]) else ""
        self.assertEqual(factory.call_args.kwargs["spacecall_url"], expected_base)
        bus = EventBus()

        def received(event):
            self.events.append(event)
            if event.get("auto"):
                self.delivered.set()

        bus.subscribe("risk_contact", received)
        call = kwargs.pop("call", lambda *args: self.opened.append(args) or {"opened": True})
        wire = RiskWire(bus, snapshot_fn=lambda: [], contact=trusted, call=call, **kwargs)
        self.addCleanup(wire.stop)
        return wire

    def test_defaults_and_config_validation(self):
        self.assertEqual(DEFAULTS["risk"]["call_service"], "meet")
        self.assertEqual(DEFAULTS["risk"]["spacecall_url"], "")
        for url in ("", BASE, "https://calls.example", BASE + "?version=1"):
            errors, warnings = validate(merge({"risk": {"call_service": "spacecall", "spacecall_url": url}}))
            self.assertEqual((errors, warnings), ([], []))
        for settings in ({"call_service": "other"}, {"call_service": 1}, {"spacecall_url": 1}):
            self.assertTrue(validate(merge({"risk": settings}))[0])

    def test_invalid_spacecall_base_urls_are_rejected(self):
        for url in ("http://calls.example/", "https://calls.example:443/", "https://calls.example:8443/",
                    "https://calls.example:/", "https://calls.example:bad/", "https://user@calls.example/",
                    "https://@calls.example/", BASE + "#", BASE + "#room=x", "https:///", "https://",
                    BASE + " ", "\n" + BASE, BASE + "\x00", "https://calls.example\\@evil.example/"):
            with self.subTest(url=url):
                self.assertFalse(contact.valid_spacecall_url(url))
                errors, _ = validate(merge({"risk": {"spacecall_url": url}}))
                self.assertTrue(any("risk.spacecall_url" in error for error in errors))

    def test_call_urls_require_the_configured_origin_and_path_prefix(self):
        for base in (BASE, BASE.rstrip("/")):
            for url in (BASE + "#room=x&role=helper", BASE + "child/#room=x"):
                self.assertTrue(contact.valid_call_url(url, base), (url, base))
            for url in ("https://evil.example/ally-call/#room=x", BASE.replace(".io", ".io.evil.example"),
                        BASE.replace("/ally-call/", "/ally-call-evil/"), BASE + "../other/#room=x",
                        BASE + "%2e%2e/other/", BASE.replace("https:", "http:"),
                        BASE.replace(".io/", ".io:443/"), BASE.replace("https://", "https://user@")):
                self.assertFalse(contact.valid_call_url(url, base), (url, base))
        self.assertFalse(contact.valid_call_url(BASE + "#room=x"))
        self.assertFalse(contact.valid_call_url(BASE, "http://ganeshkar1.github.io/ally-call/"))
        self.assertTrue(contact.valid_call_url(MEET, BASE))

    def test_manual_calls_make_fresh_rooms_and_encode_both_roles(self):
        name = "Priya & 李 /?#"
        wire = self.make_wire({"trusted_contact": name})
        rooms = []
        for _ in range(2):
            self.assertEqual(wire._tell(RISK), f"Opening a call with {name}. I've asked them to join.")
            action, args = self.opened[-1]
            self.assertEqual(action, "openCall")
            self.assertEqual(args["service"], "spacecall")
            self.assertEqual(args["spacecallOrigin"], "https://ganeshkar1.github.io")
            elder = fragment(args["url"])
            helper = fragment(self.sent[-1][1])
            self.assertEqual(elder, dict(helper, role="elder"))
            self.assertEqual(helper, {"room": elder["room"], "role": "helper", "name": name, "kind": RISK.kind})
            self.assertRegex(elder["room"], r"^[A-Za-z0-9_-]{22}$")
            self.assertIn("name=Priya%20%26%20", args["url"])
            self.assertTrue(args["url"].startswith(BASE + "#room="))
            self.assertNotIn(elder["room"], json.dumps(self.events))
            rooms.append(elder["room"])
        self.assertNotEqual(*rooms)
        self.assertEqual(self.events[-1], {"name": name, "kind": RISK.kind, "ok": True,
                                          "call_opened": True, "service": "spacecall"})

    def test_the_push_click_and_join_action_use_the_helper_url(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        # A long encoded name still produces a valid helper link.
        wire = self.make_wire({"trusted_contact": "李" * 30})
        wire.contact = (wire.contact[0], contact.ntfy_sender("ally-priya-7f3k29qx", spacecall_url=BASE),
                        *wire.contact[2:])
        requests = []
        with patch("urllib.request.urlopen", lambda req, timeout: requests.append(req) or Response()):
            wire._tell(RISK)
        helper = requests[0].get_header("Click")
        self.assertEqual(fragment(helper)["role"], "helper")
        self.assertEqual(fragment(helper)["room"], fragment(self.opened[0][1]["url"])["room"])
        self.assertIn(helper, requests[0].get_header("Actions"))
        self.assertNotIn("room=", requests[0].data.decode())

    def test_pending_auto_alert_and_later_call_share_the_room_and_push(self):
        entered, release, asked, finished = (threading.Event() for _ in range(4))
        offers, attempts, notes = [], [], []

        def send(*args):
            attempts.append(args)
            entered.set()
            release.wait(3)
            return True

        def ask(offer):
            offers.append(offer)
            asked.set()
            return "timeout"

        wire = self.make_wire({"auto_alert": True}, send=send, ask=ask)
        try:
            wire.on_risk_warning({"risk": RISK.to_dict(), "text": "x"})
            self.assertTrue(entered.wait(2))
            self.assertTrue(asked.wait(2))
            self.assertEqual(offers[0].extra_action[0], "Call Priya")
            self.assertEqual(len(self.opened), 1)  # automatic alerts open the PC's call before sending

            def tap():
                try:
                    notes.append(offers[0].extra_action[1]())
                finally:
                    finished.set()

            threading.Thread(target=tap, daemon=True).start()
            self.assertFalse(finished.wait(0.05))
        finally:
            release.set()
        self.assertTrue(finished.wait(2))
        self.assertTrue(self.delivered.wait(2))
        self.assertEqual(notes, ["Opening a call with Priya. They already got the alert."])
        self.assertEqual(len(attempts), 1)
        room = fragment(attempts[0][1])["room"]
        self.assertEqual([fragment(args["url"])["room"] for _, args in self.opened], [room, room])
        offers[0].extra_action[1]()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(fragment(self.opened[-1][1]["url"])["room"], room)
        self.assertNotIn(room, json.dumps(self.events))
        self.assertTrue(all(event["service"] == "spacecall" for event in self.events))

    def test_new_auto_alert_incidents_have_new_rooms(self):
        now = [0]
        wire = self.make_wire({"auto_alert": True}, clock=lambda: now[0])
        for when in (0, 300):
            now[0] = when
            self.delivered.clear()
            wire.on_risk_warning({"risk": RISK.to_dict(), "text": "x"})
            self.assertTrue(self.delivered.wait(2))
        self.assertNotEqual(fragment(self.sent[0][1])["room"], fragment(self.sent[1][1])["room"])
        self.assertTrue(all(event["auto"] and event["call_opened"] for event in self.events))

    def test_missing_or_invalid_spacecall_falls_back_to_meet_then_tell(self):
        for url in ("", "http://calls.example/", BASE + "#room=x", "https://calls.example:443/"):
            for meet in (MEET, ""):
                with self.subTest(url=url, meet=meet):
                    wire = self.make_wire({"spacecall_url": url, "contact_call_url": meet})
                    note = wire._tell(RISK)
                    if meet:
                        self.assertIn("Opening a call", note)
                        self.assertEqual(self.opened, [("openCall", {"url": MEET, "name": "Priya"})])
                        self.assertEqual(self.sent[0][1], MEET)
                    else:
                        self.assertEqual(note, "I've sent Priya a message.")
                        self.assertEqual(self.opened, [])
                        self.assertEqual(len(self.sent[0]), 1)
                    self.assertNotIn("service", self.events[0])
        wire = self.make_wire({"call_service": "meet", "contact_call_url": MEET})
        wire._tell(RISK)
        self.assertEqual(self.opened[0][1]["url"], MEET)

    def test_open_call_fallback_keeps_the_elder_url(self):
        for result in (None, {"opened": False}, OSError("unavailable")):
            calls = []

            def call(action, args):
                calls.append((action, args))
                if action == "openCall":
                    if isinstance(result, Exception):
                        raise result
                    return result
                return args["url"]

            wire = self.make_wire(call=call)
            self.assertIn("Opening a call", wire._tell(RISK))
            self.assertEqual(calls[1], ("openUrl", {"url": calls[0][1]["url"], "newTab": True}))
            self.assertEqual(fragment(calls[1][1]["url"])["role"], "elder")
            self.assertEqual(fragment(self.sent[0][1])["role"], "helper")

    def test_failures_never_log_the_room(self):
        urls = []

        def call(action, args):
            urls.append(args["url"])
            raise RuntimeError(args["url"])

        def send(message, url):
            urls.append(url)
            raise RuntimeError(url)

        wire = self.make_wire(call=call, send=send)
        with self.assertLogs("ally", level="DEBUG") as logs:
            self.assertIn("couldn't reach", wire._tell(RISK))
        room = fragment(urls[0])["room"]
        self.assertNotIn(room, "\n".join(logs.output))
        self.assertNotIn(room, json.dumps(self.events))

    def test_call_ended_hook_publishes_only_the_service(self):
        bus, events = EventBus(), []
        bus.subscribe("risk_call_ended", events.append)
        bridge = SimpleNamespace(on_page=None)
        agent = SimpleNamespace(bus=bus, bridge=bridge)
        with patch("ally.risk.wire._make_guardian", return_value=(None, None)), \
                patch.object(RiskWire, "start"):
            wire = attach(agent)
        self.addCleanup(wire.stop)
        for service in ("meet", "spacecall", "other"):
            bridge.on_call_ended({"service": service, "room": "private"})
        self.assertEqual(events, [{"service": "meet"}, {"service": "spacecall"}])

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the extension check")
    def test_extension_allowlist_skin_and_call_window_close(self):
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("extension/background.js", "utf8");
const action = source.slice(source.indexOf("  async openCall("), source.indexOf("  async rememberSite("));
const events = source.slice(source.indexOf("async function insertCallCSS("), source.indexOf("// ---------------------------------------------------------------- shortcuts"));
const stored = {}, css = [], sent = [], created = [];
let removed, next = 0;
const context = {
  URL, send: (msg) => sent.push(msg),
  chrome: {
    storage: { session: {
      get: async (key) => structuredClone(key === null ? stored : { [key]: stored[key] }),
      set: async (values) => Object.assign(stored, values),
      remove: async (keys) => { for (const key of [].concat(keys)) delete stored[key]; },
    } },
    scripting: { insertCSS: async (args) => css.push(args) },
    windows: {
      create: async (args) => { created.push(args); return { id: ++next, tabs: [{ id: next, status: "complete" }] }; },
      remove: async () => {},
      onRemoved: { addListener: (fn) => { removed = fn; } },
    },
  },
};
vm.runInNewContext(`let callCreation = Promise.resolve(); const callKey = (id) => 'allyCall:' + id;
  const ACTIONS = { ${action} }; globalThis.actions = ACTIONS; ${events}`, context);
(async () => {
  const origin = "https://ganeshkar1.github.io";
  const url = origin + "/ally-call/#room=private&role=elder";
  const options = { url, name: "Priya", service: "spacecall", spacecallOrigin: origin };
  for (const args of [
    { url }, { ...options, spacecallOrigin: "" }, { ...options, url: "https://evil.example/" },
    { ...options, url: "http://ganeshkar1.github.io/" }, { ...options, url: "https://user@ganeshkar1.github.io/" },
    { ...options, url: "https://ganeshkar1.github.io:8443/" },
    ...[origin + "/path", origin + ":443", origin + ":8443", origin + "#x", "http://calls.example",
        "https://user@calls.example"].map((spacecallOrigin) => ({ ...options, spacecallOrigin })),
  ]) await assert.rejects(context.actions.openCall(args));
  assert.equal(created.length, 0);
  const space = await context.actions.openCall(options);
  assert.equal(created[0].type, "popup");
  assert.equal(created[0].state, "maximized");
  await context.insertCallCSS(space.windowId); // reloads must not inject the Meet skin either
  assert.equal(css.length, 0);
  const meet = await context.actions.openCall({ url: "https://meet.google.com/abc-defg-hij", name: "Priya" });
  assert.equal(css.length, 1);
  await removed(999); // unrelated windows have no call-ended event
  assert.equal(sent.length, 0);
  await removed(space.windowId);
  assert.deepEqual(JSON.parse(JSON.stringify(sent)), [{ type: "event", name: "risk_call_ended", service: "spacecall" }]);
  const closing = removed(meet.windowId);
  delete stored["allyCall:" + meet.windowId]; // End call also clears its own record
  await closing;
  assert.equal(sent[1].service, "meet");
  assert.equal(Object.keys(stored).length, 0);
  assert.ok(!JSON.stringify(sent).includes("private"));
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
