import contextlib
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
    def setUp(self):
        pin = patch("ally.risk.wire.callwindow.keep_on_top", return_value=None)
        pin.start()
        self.addCleanup(pin.stop)

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

    def test_auto_alert_has_no_join_headers_or_room_until_the_call_tap(self):
        offers, requests, asked = [], [], threading.Event()

        def ask(offer):
            offers.append(offer)
            asked.set()
            return "timeout"

        def post(req, timeout):
            requests.append(req)
            return contextlib.nullcontext(SimpleNamespace(status=200))

        wire = self.make_wire({"auto_alert": True}, ask=ask,
                              send=contact.ntfy_sender("ally-priya-7f3k29qx", spacecall_url=BASE))
        with patch("urllib.request.urlopen", post), \
                patch("ally.risk.wire.secrets.token_urlsafe", return_value="created-on-tap") as room:
            wire.on_risk_warning({"risk": RISK.to_dict(), "text": "x"})
            self.assertTrue(self.delivered.wait(2))
            self.assertTrue(asked.wait(2))
            self.assertEqual(len(requests), 1)
            self.assertIsNone(requests[0].get_header("Click"))
            self.assertIsNone(requests[0].get_header("Actions"))
            self.assertNotIn(BASE, requests[0].data.decode())
            self.assertIn("If they tap Call", requests[0].data.decode())
            self.assertEqual(self.opened, [])
            room.assert_not_called()
            self.assertEqual(offers[0].extra_action[0], "Call Priya")
            self.assertEqual(offers[0].extra_action[1](), "Opening a call with Priya. I've asked them to join.")
            room.assert_called_once_with(16)
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.opened[0][0], "openCall")
        self.assertEqual(fragment(self.opened[0][1]["url"])["role"], "elder")
        self.assertEqual(len(requests), 2)
        helper = requests[1].get_header("Click")
        self.assertEqual(fragment(helper), dict(fragment(self.opened[0][1]["url"]), role="helper"))
        self.assertIn(helper, requests[1].get_header("Actions"))
        self.assertEqual(requests[1].data.decode(), "They're calling you through Ally. Tap to join.")
        self.assertFalse(self.events[0]["call_opened"])

    def test_auto_alert_without_a_browser_bridge_does_not_open_a_browser(self):
        wire = self.make_wire({"auto_alert": True}, call=None)
        with patch("webbrowser.open") as browser:
            wire.on_risk_warning({"risk": RISK.to_dict(), "text": "x"})
            self.assertTrue(self.delivered.wait(2))
            browser.assert_not_called()
        self.assertEqual(self.sent, [(contact.message_for(RISK.kind, call_available=True),)])
        self.assertFalse(self.events[0]["call_opened"])

    def test_auto_alert_off_waits_for_the_call_tap(self):
        offers, asked = [], threading.Event()

        def ask(offer):
            offers.append(offer)
            asked.set()
            return "timeout"

        wire = self.make_wire({"auto_alert": False}, ask=ask)
        wire.on_risk_warning({"risk": RISK.to_dict(), "text": "x"})
        self.assertTrue(asked.wait(2))
        self.assertEqual((self.sent, self.opened), ([], []))
        self.assertEqual(offers[0].extra_action[1](), "Opening a call with Priya. I've asked them to join.")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(fragment(self.sent[0][1])["role"], "helper")

    def test_pending_auto_alert_and_later_call_use_a_separate_invitation_and_reuse_the_room(self):
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
            self.assertEqual(self.opened, [])
            self.assertEqual(attempts, [(contact.message_for(RISK.kind, call_available=True),)])

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
        self.assertEqual(notes, ["Opening a call with Priya. I've asked them to join."])
        self.assertEqual(len(attempts), 2)
        room = fragment(attempts[1][1])["room"]
        self.assertEqual([fragment(args["url"])["room"] for _, args in self.opened], [room])
        offers[0].extra_action[1]()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(fragment(self.opened[-1][1]["url"])["room"], room)
        self.assertNotIn(room, json.dumps(self.events))
        self.assertTrue(all(event["service"] == "spacecall" for event in self.events))

    def test_new_auto_alert_incidents_get_new_rooms_only_on_call(self):
        now = [0]
        wire = self.make_wire({"auto_alert": True}, clock=lambda: now[0])
        alerts = []
        for when in (0, 300):
            now[0] = when
            self.delivered.clear()
            alerts.append(wire._auto_alert(RISK))
            self.assertTrue(self.delivered.wait(2))
        self.assertEqual(self.opened, [])
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(len(args) == 1 for args in self.sent))
        self.assertTrue(all(event["auto"] and not event["call_opened"] for event in self.events))
        for alert in alerts:
            wire._tell(RISK, alert)
        self.assertNotEqual(fragment(self.sent[2][1])["room"], fragment(self.sent[3][1])["room"])

    def test_failed_invitation_is_retried_in_the_same_room_after_any_auto_alert_result(self):
        for auto_ok in (True, False):
            with self.subTest(auto_ok=auto_ok):
                attempts = []

                def send(*args):
                    attempts.append(args)
                    return auto_ok if len(attempts) == 1 else len(attempts) > 2

                wire = self.make_wire({"auto_alert": True}, send=send)
                alert = wire._auto_alert(RISK)
                self.assertTrue(self.delivered.wait(2))
                self.assertIn("couldn't reach Priya", wire._tell(RISK, alert))
                self.assertEqual(wire._tell(RISK, alert), "Opening a call with Priya. I've asked them to join.")
                self.assertEqual(len(attempts), 3)
                self.assertEqual(len(attempts[0]), 1)
                self.assertEqual(attempts[1], attempts[2])
                self.assertEqual(self.opened[0][1]["url"], self.opened[1][1]["url"])

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
    def test_helper_actions_trust_only_saved_call_tabs_and_target_the_normal_window(self):
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("extension/background.js", "utf8");
const worker = source.slice(source.indexOf("async function handleHelperAction("), source.indexOf("// ---------------------------------------------------------------- start up"));
const stored = { "allySpacecall:10": { tabId: 10, windowId: 20, name: "Priya" } };
const changed = [], notes = [];
let listener, normal = { id: 30, type: "normal" }, tab = { id: 40, url: "https://shop.test" }, bridge = true, fail = false;
const context = {
  callCreation: Promise.resolve(), stepZoom: (zoom, dir) => zoom + dir * .1,
  send: (msg) => { if (bridge) notes.push(msg); return bridge; },
  chrome: {
    storage: { session: { get: async (key) => ({ [key]: stored[key] }) } },
    windows: { getLastFocused: async (opts) => { assert.deepEqual(Array.from(opts.windowTypes), ["normal"]); return normal; } },
    tabs: {
      query: async (opts) => { assert.equal(opts.active, true); assert.equal(opts.windowId, 30); return tab ? [tab] : []; },
      remove: async (id) => { if (fail) throw new Error("gone"); changed.push(["close", id]); },
      getZoom: async (id) => { assert.equal(id, 40); return 1.25; },
      setZoom: async (id, zoom) => { changed.push(["zoom", id, zoom]); },
    },
    runtime: { onMessage: { addListener: (fn) => { listener = fn; } } },
  },
};
vm.runInNewContext(worker, context);
const sender = { tab: { id: 10, windowId: 20 }, frameId: 0 };
const message = { kind: "allyHelperAction", seq: 1, action: "close_tab", x: 0, y: 0 };
const request = async (msg = message, from = sender) => {
  let done;
  const reply = new Promise(resolve => { done = resolve; });
  assert.equal(listener(msg, from, done), true);
  return await reply;
};
(async () => {
  for (const from of [{}, { tab: { id: 99, windowId: 20 } }, { ...sender, frameId: 1 }, { tab: { id: 10, windowId: 99 } }]) {
    assert.equal((await request(message, from)).ok, false);
  }
  for (const bad of [{ action: "click" }, { action: "type" }, { action: "scroll" }, { seq: -1 }, { seq: 1.5 },
    { seq: 2 ** 32 }, { x: NaN }, { y: Infinity }, { action: "point", y: 1.1 }, { action: "point", x: -.1 }, { x: .2 }]) {
    assert.equal((await request({ ...message, ...bad })).ok, false);
  }
  assert.equal(changed.length, 0); assert.equal(notes.length, 0);
  assert.equal((await request()).ok, true);
  assert.deepEqual(changed[0], ["close", 40]);
  assert.equal(notes[0].name, "helper_action"); assert.equal(notes[0].helper_name, "Priya");
  assert.equal(notes[0].tab_id, 40); assert.equal(notes[0].ok, true);
  assert.equal((await request({ ...message, action: "bigger" })).ok, true);
  assert.deepEqual(changed[1], ["zoom", 40, 1.35]);
  assert.equal((await request({ ...message, action: "point", x: .5, y: .75 })).ok, true);
  assert.equal(changed.length, 2); assert.equal(notes[2].x, .5); assert.equal(notes[2].y, .75);
  bridge = false;
  assert.equal((await request({ ...message, action: "point" })).ok, false);
  bridge = true;
  for (const url of ["chrome://settings", "chrome-extension://abc/popup.html", "about:blank"]) {
    tab.url = url; assert.equal((await request()).ok, false);
  }
  tab = null; assert.equal((await request()).ok, false);
  normal = null; assert.equal((await request()).ok, false);
  normal = { id: 20, type: "normal" }; assert.equal((await request()).ok, false);
  normal = { id: 30, type: "popup" }; assert.equal((await request()).ok, false);
  normal = { id: 30, type: "normal" }; tab = { id: 40, url: "https://shop.test" }; fail = true;
  assert.equal((await request()).ok, false);
  assert.equal(changed.length, 2, "invalid targets cannot change any tab");

  // Content relay rejects cross-window, cross-origin and unrelated page messages.
  let receive; const forwarded = [], replies = [];
  const page = { addEventListener: (_type, fn) => { receive = fn; }, postMessage: (data, origin) => replies.push({ data, origin }) };
  const origin = "https://calls.test";
  vm.runInNewContext(fs.readFileSync("extension/call-relay.js", "utf8"), {
    window: page, location: { origin }, chrome: { runtime: { sendMessage: async (data) => { forwarded.push(data); return { ok: true }; } } },
  });
  const data = { source: "ally-call", type: "helperAction", seq: 8, action: "point", x: .2, y: .8 };
  for (const event of [{ source: {}, origin, data }, { source: page, origin: "https://evil.test", data },
    { source: page, origin, data: { ...data, source: "evil" } }, { source: page, origin, data: { ...data, type: "click" } }]) await receive(event);
  assert.equal(forwarded.length, 0);
  await receive({ source: page, origin, data });
  assert.equal(forwarded.length, 1); assert.equal(forwarded[0].kind, "allyHelperAction");
  assert.equal(forwarded[0].seq, 8); assert.equal(replies[0].data.ok, true);
  assert.equal(replies[0].data.source, "ally-ext"); assert.equal(replies[0].data.type, "helperActionResult");
  assert.equal(replies[0].origin, origin);
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the extension check")
    def test_extension_allowlist_skin_and_call_window_close(self):
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("extension/background.js", "utf8").replace(/\r\n/g, "\n");
const action = source.slice(source.indexOf("  async openCall("), source.indexOf("  async rememberSite("));
const docked = source.slice(source.indexOf("async function openMiniCall("), source.indexOf("const ACTIONS = {"));
const titleStart = source.indexOf('async function insertCallCSS(');
const events = source.slice(titleStart, source.indexOf("// ---------------------------------------------------------------- shortcuts"));
const stored = {}, css = [], sent = [], created = [], updated = [];
let removed, titleChanged, status = "ringing", next = 0, browser = null, displays = null;
const context = {
  URL, send: (msg) => sent.push(msg),
  chrome: {
    storage: { session: {
      get: async (key) => structuredClone(key === null ? stored : { [key]: stored[key] }),
      set: async (values) => Object.assign(stored, values),
      remove: async (keys) => { for (const key of [].concat(keys)) delete stored[key]; },
    } },
    scripting: {
      insertCSS: async (args) => css.push(args),
      executeScript: async () => [{ result: status }],
    },
    tabs: { onUpdated: { addListener: (fn) => { titleChanged = fn; } } },
    windows: {
      create: async (args) => { created.push(args); return { id: ++next, tabs: [{ id: next, status: "complete" }] }; },
      remove: async () => {},
      getLastFocused: async () => browser,
      update: async (id, info) => { updated.push({ id, info }); },
      onRemoved: { addListener: (fn) => { removed = fn; } },
    },
    get system() { return displays && { display: { getInfo: async () => displays } }; },
  },
};
vm.runInNewContext(`let callCreation = Promise.resolve(); const callKey = (id) => 'allyCall:' + id;
  ${docked}
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
  assert.equal(created[0].width, 380);
  assert.equal(created[0].height, 360);
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

  // Ring and connect in the same corner, without a browser-bounds record or listener.
  browser = { id: 50, type: "normal", state: "maximized", left: 0, top: 0, width: 1600, height: 900 };
  const miniCall = await context.actions.openCall(options);
  const popup = created[created.length - 1];
  assert.equal(popup.type, "popup");
  assert.equal(popup.left + popup.width, 1600 - 16);
  assert.equal(popup.top + popup.height, 900 - 16);
  assert.equal(popup.width, 380);
  assert.equal(popup.height, 360);
  assert.equal(titleChanged, undefined);
  assert.equal(updated.length, 0);
  browser = { ...browser, state: "normal", width: 1000 };
  const fallback = await context.actions.openCall(options);
  assert.equal(created[created.length - 1].width, 380);
  assert.equal(created[created.length - 1].height, 360);
  await removed(miniCall.windowId);
  await removed(fallback.windowId);
  assert.equal(updated.length, 0);
  assert.equal(Object.keys(stored).length, 0);
})().catch((error) => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
