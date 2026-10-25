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
const stored = {}, created = [], updated = [], sent = [], scripts = [];
let browser = { id: 50, type: "normal", state: "maximized", left: 0, top: 0, width: 1600, height: 900 };
let displays = null, pageWorkArea = null, status = "ringing", failWindow = null, next = 0;
function worker() {
  let titleChanged, removed;
  const context = { URL, send: message => sent.push(message), chrome: {
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

    def test_popup_stays_small_on_connection_and_close_across_worker_restarts(self):
        self.run_js(r'''
const w = worker(), call = await w.open(options), id = call.windowId;
assert.deepEqual(plain(created[0]), { url: options.url, type: "popup", focused: true,
  left: 1204, top: 524, width: 380, height: 360 });
assert.equal(w.titleChanged, undefined, "there is no connected-title resize listener");
assert.deepEqual(Object.keys(stored['allySpacecall:' + id]).sort(), ['name', 'service', 'tabId', 'windowId']);
status = "connected";
assert.equal(worker().titleChanged, undefined);
await worker().removed(id);
assert.equal(updated.length, 0);
assert.equal(scripts.length, 0);
assert.equal(sent[0].service, "spacecall");
assert.equal(Object.keys(stored).length, 0);
assert.ok(!JSON.stringify(sent).includes("0123456789abcdef"));
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
