"""Exercise the real call-window code with in-memory Chrome APIs, without starting Ally."""

import shutil
import subprocess
import unittest

from helpers import ROOT


HARNESS = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("extension/background.js", "utf8").replace(/\r\n/g, "\n");
const docking = source.slice(source.indexOf("async function openMiniCall("), source.indexOf("const ACTIONS = {"));
const action = source.slice(source.indexOf("  async openCall("), source.indexOf("  async rememberSite("));
const titleStart = source.indexOf('async function insertCallCSS(');
const events = source.slice(titleStart, source.indexOf("// ---------------------------------------------------------------- shortcuts"));
const stored = {}, created = [], updated = [], sent = [], scripts = [], closed = [], removals = [];
const settle = async () => { await Promise.all(removals.splice(0)); };
let browser = { id: 50, type: "normal", state: "maximized", left: 0, top: 0, width: 1600, height: 900 };
let displays = null, pageWorkArea = null, status = "ringing", failWindow = null, next = 0;
let now = 1000, removalGate = null, failRemove = null;
function worker() {
  let titleChanged, removed, timerId = 0;
  const timers = new Map();
  const context = { URL, Date: { now: () => now },
    setTimeout: (fn, delay) => { timers.set(++timerId, { fn, at: now + delay }); return timerId; },
    clearTimeout: id => timers.delete(id), send: message => sent.push(message), chrome: {
    storage: { session: {
      get: async key => structuredClone(key === null ? stored : { [key]: stored[key] }),
      set: async values => Object.assign(stored, structuredClone(values)),
      remove: async keys => { for (const key of [].concat(keys)) delete stored[key]; },
    } },
    tabs: { query: async () => [{ id: 42 }], onUpdated: { addListener: fn => { titleChanged = fn; } } },
    scripting: {
      executeScript: async args => { scripts.push(args); return [{ result: pageWorkArea || status }]; },
      insertCSS: async () => {},
    },
    windows: {
      getLastFocused: async () => browser,
      create: async args => { created.push(args); return { id: ++next, tabs: [{ id: next, status: "complete" }] }; },
      update: async (id, info) => {
        if (id === failWindow) throw new Error("Window unavailable");
        updated.push({ id, info });
      },
      remove: async id => {
        closed.push(id);
        if (id === failRemove) throw new Error("Already closed");
        if (removalGate) await removalGate;
        removals.push(removed(id)); // Chrome does not await its event listeners.
      },
      onRemoved: { addListener: fn => { removed = fn; } },
    },
    get system() { return displays && { display: { getInfo: async () => displays } }; },
  } };
  vm.runInNewContext(`let callCreation = Promise.resolve(); const callKey = id => 'allyCall:' + id;
    ${docking} const ACTIONS = { ${action} }; globalThis.open = ACTIONS.openCall; ${events}`, context);
  return { open: context.open, titleChanged, removed, timers,
    async tick(ms) {
      now += ms;
      for (const [id, timer] of timers) {
        if (timer.at <= now) { timers.delete(id); timer.fn(); }
      }
      await settle();
    },
  };
}
const options = { url: "https://calls.example/#room=0123456789abcdef&role=elder&name=Priya", name: "Priya",
  service: "spacecall", spacecallOrigin: "https://calls.example" };
const plain = value => JSON.parse(JSON.stringify(value));
const title = (w, id, text = "New title") => w.titleChanged(id, { title: text }, { windowId: id });
'''


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for call-window checks")
class CallRingTests(unittest.TestCase):
    def run_js(self, body):
        script = HARNESS + "\n(async () => {\n" + body + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
        result = subprocess.run([shutil.which("node"), "-"], input=script, text=True,
                                capture_output=True, cwd=ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_popup_stays_small_on_connection_and_close_across_worker_restarts(self):
        self.run_js(r'''
const w = worker(), call = await w.open(options), id = call.windowId;
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true,
  left: 1204, top: 524, width: 380, height: 360 });
await title(w, id);
assert.equal(w.timers.size, 0);
assert.deepEqual(Object.keys(stored['allySpacecall:' + id]).sort(), ['name', 'service', 'tabId', 'windowId']);
status = "connected";
const restarted = worker();
await title(restarted, id);
assert.equal(restarted.timers.size, 0);
await restarted.removed(id);
assert.equal(updated.length, 0);
assert.equal(scripts.length, 2);
assert.equal(sent[0].service, "spacecall");
assert.equal(Object.keys(stored).length, 0);
assert.ok(!JSON.stringify(sent).includes("0123456789abcdef"));
''')

    def test_ended_popup_closes_once_after_four_seconds_through_the_existing_event(self):
        self.run_js(r'''
const w = worker(), { windowId: id } = await w.open(options);
status = "ended";
await Promise.all([title(w, id), title(w, id)]);
assert.equal(w.timers.size, 1);
assert.equal(stored['allySpacecall:' + id].closeAt, 5000);
assert.equal(vm.runInNewContext('(' + scripts[0].func + ')()', {
  document: { documentElement: { dataset: { allyCall: 'ended' } } }
}), 'ended');
await w.tick(3999);
assert.equal(closed.length, 0);
await title(w, id, 'Still ended');
assert.equal(w.timers.size, 1);
let release;
removalGate = new Promise(resolve => { release = resolve; });
await w.tick(1);
assert.deepEqual(closed, [id]);
await title(w, id);
await w.tick(0);
assert.deepEqual(closed, [id], 'events during removal cannot queue another close');
release();
await new Promise(resolve => setImmediate(resolve));
await settle();
assert.deepEqual(plain(sent), [{ type: 'event', name: 'risk_call_ended', service: 'spacecall' }]);
assert.equal(Object.keys(stored).length, 0);
await title(w, id);
await w.tick(4000);
assert.deepEqual(closed, [id]);
''')

    def test_next_tab_event_rearms_the_original_deadline_after_worker_restart(self):
        self.run_js(r'''
const w = worker(), { windowId: id } = await w.open(options);
status = "ended";
await title(w, id);
await w.tick(1500);
const restarted = worker(); // Old worker timers are lost, session records survive.
await title(restarted, 999);
assert.equal(scripts.length, 1, 'the saved deadline does not need another page read');
await restarted.tick(2499);
assert.equal(closed.length, 0);
await restarted.tick(1);
assert.deepEqual(closed, [id]);
assert.equal(sent[0].service, 'spacecall');
''')

    def test_restart_can_detect_ended_without_a_title_change_and_recover_an_overdue_timer(self):
        self.run_js(r'''
const w = worker(), { windowId: id } = await w.open(options);
status = "ended";
const restarted = worker();
await restarted.titleChanged(id, {}, { windowId: id });
now += 5000;
const overdue = worker();
await title(overdue, 999);
await overdue.tick(0);
assert.deepEqual(closed, [id]);
assert.equal(sent[0].service, 'spacecall');
''')

    def test_second_open_waits_for_the_first_window_to_close(self):
        self.run_js(r'''
const w = worker(), first = await w.open(options);
let release;
removalGate = new Promise(resolve => { release = resolve; });
const opening = w.open(options);
for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve));
assert.deepEqual(closed, [first.windowId]);
assert.equal(created.length, 1, 'the replacement must wait for removal');
release();
const second = await opening;
await settle();
assert.equal(created.length, 2);
assert.deepEqual(Object.keys(stored), ['allySpacecall:' + second.windowId]);
// A replaced window is not a call ending: Ally must keep the new call's screen share.
assert.ok(!sent.some(m => m.name === 'risk_call_ended'));
''')

    def test_new_call_removes_all_recorded_spacecall_windows_and_ignores_stale_windows(self):
        self.run_js(r'''
stored['allySpacecall:800'] = { tabId: 800, windowId: 800, service: 'spacecall' };
stored['allySpacecall:801'] = { tabId: 801, windowId: 801, service: 'spacecall' };
stored['allyCall:700'] = { tabId: 700, windowId: 700, service: 'meet' };
failRemove = 800;
const w = worker();
const [first, second] = await Promise.all([w.open(options), w.open(options)]);
await settle();
assert.deepEqual(closed.slice(0, 2), [800, 801]);
assert.equal(closed.at(-1), first.windowId);
assert.ok(!closed.includes(700));
assert.ok(stored['allyCall:700']);
assert.ok(stored['allySpacecall:' + second.windowId]);
assert.equal(stored['allySpacecall:' + first.windowId], undefined);
''')

    def test_unsaved_and_meet_tabs_cannot_schedule_auto_close(self):
        self.run_js(r'''
const w = worker(), meet = await w.open({ url: 'https://meet.google.com/abc-defg-hij' });
status = 'ended';
await title(w, 999, 'Call ended');
await title(w, meet.windowId, 'Call ended');
await w.tick(4000);
assert.equal(scripts.length, 0);
assert.equal(w.timers.size, 0);
assert.equal(closed.length, 0);
''')

    def test_manual_close_cancels_the_ended_timer(self):
        self.run_js(r'''
const w = worker(), { windowId: id } = await w.open(options);
status = 'ended';
await title(w, id);
await w.removed(id);
assert.equal(w.timers.size, 0);
await w.tick(4000);
assert.equal(closed.length, 0);
assert.equal(sent.length, 1);
''')

    def test_monitor_offsets_keep_the_sixteen_pixel_inset(self):
        self.run_js(r'''
browser = { id: 50, type: "normal", state: "normal", left: -1700, top: 150, width: 1000, height: 700 };
displays = [{ bounds: { left: -1920, top: 0, width: 1920, height: 1080 },
  workArea: { left: -1920, top: 40, width: 1920, height: 1040 } }];
const w = worker(), { windowId: id } = await w.open(options);
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true,
  left: -396, top: 704, width: 380, height: 360 });
await w.removed(id);
assert.equal(updated.length, 0);
''')

    def test_normal_browser_uses_the_page_work_area_without_display_permission(self):
        self.run_js(r'''
browser.state = "normal";
pageWorkArea = { left: -1920, top: 40, width: 1920, height: 1040 };
const w = worker(), { windowId: id } = await w.open(options);
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true,
  left: -396, top: 704, width: 380, height: 360 });
assert.equal(scripts.length, 1);
assert.equal(scripts[0].target.tabId, 42);
await w.removed(id);
assert.equal(updated.length, 0);
''')

    def test_unknown_work_area_still_opens_a_mini_player_and_meet_is_unchanged(self):
        self.run_js(r'''
const w = worker();
browser.state = "normal";
const { windowId: id } = await w.open(options);
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true, width: 380, height: 360 });
await w.removed(id);
const meet = await w.open({ url: "https://meet.google.com/abc-defg-hij" });
assert.equal(created[1].state, "maximized");
await w.removed(meet.windowId);
assert.equal(updated.length, 0);
assert.equal(sent[1].service, "meet");
''')

    def test_old_session_records_never_restore_browser_bounds(self):
        self.run_js(r'''
stored['allySpacecall:1'] = { windowId: 1, service: 'spacecall', browserShrunk: true,
  browserWindowId: 50, browserBounds: { left: 0, top: 0, width: 1600, height: 900, state: 'maximized' } };
await worker().removed(1);
assert.equal(updated.length, 0);
assert.equal(Object.keys(stored).length, 0);
''')


if __name__ == "__main__":
    unittest.main()
