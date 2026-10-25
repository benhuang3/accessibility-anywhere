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
const docking = source.slice(source.indexOf("async function openDockedCall("), source.indexOf("const ACTIONS = {"));
const action = source.slice(source.indexOf("  async openCall("), source.indexOf("  async rememberSite("));
const titleStart = source.indexOf('chrome.tabs.onUpdated.addListener(async (tabId, change, tab) => {\n  if (typeof change.title');
const events = source.slice(titleStart, source.indexOf("// ---------------------------------------------------------------- shortcuts"));
const stored = {}, created = [], updated = [], sent = [], scripts = [];
let browser = { id: 50, type: "normal", state: "maximized", left: 0, top: 0, width: 1600, height: 900 };
let displays = null, status = "ringing", failWindow = null, next = 0;
function worker() {
  let titleChanged, removed;
  const context = { URL, send: message => sent.push(message), chrome: {
    storage: { session: {
      get: async key => structuredClone(key === null ? stored : { [key]: stored[key] }),
      set: async values => Object.assign(stored, structuredClone(values)),
      remove: async keys => { for (const key of [].concat(keys)) delete stored[key]; },
    } },
    tabs: { onUpdated: { addListener: fn => { titleChanged = fn; } } },
    scripting: {
      executeScript: async args => { scripts.push(args); return [{ result: status }]; },
      insertCSS: async () => {},
    },
    windows: {
      getLastFocused: async () => browser,
      create: async args => { created.push(args); return { id: ++next, tabs: [{ id: next, status: "complete" }] }; },
      update: async (id, info) => {
        if (id === failWindow) throw new Error("Window unavailable");
        updated.push({ id, info });
      },
      remove: async () => {},
      onRemoved: { addListener: fn => { removed = fn; } },
    },
    get system() { return displays && { display: { getInfo: async () => displays } }; },
  } };
  vm.runInNewContext(`let callCreation = Promise.resolve(); const callKey = id => 'allyCall:' + id;
    ${docking} const ACTIONS = { ${action} }; globalThis.open = ACTIONS.openCall; ${events}`, context);
  return { open: context.open, titleChanged, removed };
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

    def test_ringing_leaves_browser_alone_and_ignores_other_tabs_and_statuses(self):
        self.run_js(r'''
const w = worker();
const call = await w.open(options), id = call.windowId;
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true,
  left: 1224, top: 544, width: 360, height: 340 });
assert.equal(updated.length, 0);
const saved = stored['allySpacecall:' + id];
assert.equal(saved.expanded, false);
assert.equal(saved.browserShrunk, false);
assert.deepEqual(saved.browserBounds, { left: 0, top: 0, width: 1600, height: 900, state: "maximized" });
await title(w, 999);
await w.titleChanged(id, { status: "complete" }, { windowId: id });
assert.equal(scripts.length, 0);
await title(w, id);
status = "ended";
await title(w, id);
assert.equal(updated.length, 0);
await w.removed(id);
assert.equal(updated.length, 0);
assert.equal(sent[0].service, "spacecall");
assert.equal(Object.keys(stored).length, 0);
''')

    def test_connection_expands_once_across_worker_restarts_and_restores_maximized(self):
        self.run_js(r'''
const call = await worker().open(options), id = call.windowId;
const w = worker(); // All geometry must come from session storage after a worker restart.
status = "connected";
await Promise.all([title(w, id), title(w, id)]);
assert.deepEqual(plain(updated), [
  { id, info: { state: "normal", left: 1120, top: 0, width: 480, height: 900 } },
  { id: 50, info: { state: "normal", left: 0, top: 0, width: 1120, height: 900 } },
]);
assert.equal(stored['allySpacecall:' + id].expanded, true);
assert.equal(stored['allySpacecall:' + id].browserShrunk, true);
await title(worker(), id);
assert.equal(updated.length, 2);
await worker().removed(id);
assert.deepEqual(plain(updated.slice(2)), [
  { id: 50, info: { state: "normal", left: 0, top: 0, width: 1600, height: 900 } },
  { id: 50, info: { state: "maximized" } },
]);
assert.equal(Object.keys(stored).length, 0);
assert.ok(!JSON.stringify(sent).includes("0123456789abcdef"));
''')

    def test_monitor_offsets_and_normal_browser_bounds_are_restored(self):
        self.run_js(r'''
browser = { id: 50, type: "normal", state: "normal", left: -1700, top: 150, width: 1000, height: 700 };
displays = [{ bounds: { left: -1920, top: 0, width: 1920, height: 1080 },
  workArea: { left: -1920, top: 40, width: 1920, height: 1040 } }];
const w = worker(), { windowId: id } = await w.open(options);
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true,
  left: -376, top: 724, width: 360, height: 340 });
status = "connected";
await title(w, id);
assert.deepEqual(plain(updated[0].info), { state: "normal", left: -520, top: 40, width: 520, height: 1040 });
await w.removed(id);
assert.deepEqual(plain(updated[2]), { id: 50, info: { state: "normal", left: -1700, top: 150, width: 1000, height: 700 } });
assert.equal(updated.length, 3);
''')

    def test_resize_failures_do_not_restore_an_unchanged_browser(self):
        self.run_js(r'''
const w = worker(), { windowId: id } = await w.open(options);
status = "connected";
failWindow = 50;
await title(w, id);
assert.equal(stored['allySpacecall:' + id].expanded, true);
assert.equal(stored['allySpacecall:' + id].browserShrunk, false);
failWindow = null;
await w.removed(id);
assert.equal(updated.length, 1); // Only the call popup was changed.
const { windowId: second } = await w.open(options);
failWindow = second;
await title(w, second);
assert.equal(stored['allySpacecall:' + second].expanded, false);
await w.removed(second);
assert.equal(updated.length, 1);
''')

    def test_fallback_and_meet_keep_their_existing_behavior(self):
        self.run_js(r'''
const w = worker();
browser.state = "normal";
const { windowId: id } = await w.open(options);
assert.equal(created[0].state, "maximized");
status = "connected";
await title(w, id);
await w.removed(id);
assert.equal(updated.length, 0);
const meet = await w.open({ url: "https://meet.google.com/abc-defg-hij" });
assert.equal(created[1].state, "maximized");
await title(w, meet.windowId);
assert.equal(scripts.length, 0);
await w.removed(meet.windowId);
assert.equal(sent[1].service, "meet");
''')


if __name__ == "__main__":
    unittest.main()
