import assert from 'node:assert/strict';
import { test } from 'node:test';
import { get } from 'svelte/store';
import { actionDisabledReason, actionLabel, createHelperActions, createElderRelay,
  normalizedPoint, isHelperAction, type HelperAction } from '../src/lib/helperActions.ts';
import HelperActionRow from '../src/module_bindings/helper_action_event_table.ts';
import ActionResultRow from '../src/module_bindings/action_result_event_table.ts';
import * as module from '../server/src/index.ts';

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const action: HelperAction = { seq: 1, action: 'point', x: 0.25, y: 0.75 };

test('normalized taps account for letterboxing, zoom and a scrolled image', () => {
  assert.deepEqual(normalizedPoint(200, 180, { left: 20, top: 80, width: 360, height: 200 }), { x: 0.5, y: 0.5 });
  assert.deepEqual(normalizedPoint(200, 180, { left: -160, top: -20, width: 720, height: 400 }), { x: 0.5, y: 0.5 });
  assert.deepEqual(normalizedPoint(20, 80, { left: 20, top: 80, width: 360, height: 200 }), { x: 0, y: 0 });
  assert.equal(normalizedPoint(0, 0, { left: 20, top: 80, width: 360, height: 200 }), null);
  for (const value of [0, -1, NaN, Infinity]) {
    assert.equal(normalizedPoint(5, 5, { left: 0, top: 0, width: value, height: 200 }), null);
  }
});

test('action payloads have bounded coordinates, known verbs and u32 sequence numbers', () => {
  assert.ok(isHelperAction(action));
  for (const bad of [null, {}, { ...action, seq: -1 }, { ...action, seq: 1.5 },
    { ...action, seq: 0x100000000 }, { ...action, action: 'click' }, { ...action, x: NaN },
    { ...action, x: '0.5' }, { ...action, y: 1.1 }, { ...action, y: -0.1 }]) assert.ok(!isHelperAction(bad));
});

test('one pending action per kind; only matching result completes it and results expire', async () => {
  const state = createHelperActions(500, 10);
  try {
    const sent: HelperAction[] = [];
    const transmit = async (row: HelperAction) => { sent.push(row); };
    const close = state.send('close_tab', transmit)!;
    assert.equal(state.send('close_tab', transmit), null);
    const bigger = state.send('bigger', transmit)!;
    await tick();
    assert.equal(sent.length, 2);
    assert.deepEqual([close.x, close.y], [0, 0]);
    state.complete(999, true);
    assert.equal(Object.keys(get(state.pending)).length, 2);
    state.complete(close.seq, true);
    assert.deepEqual(get(state.lastResult), { seq: close.seq, action: 'close_tab', ok: true });
    assert.ok(get(state.pending)[bigger.seq]);
    assert.equal(actionLabel('close_tab', false, get(state.results).close_tab), 'Closed \u2713');
    state.complete(close.seq, false);
    assert.equal(get(state.results).close_tab?.ok, true, 'late duplicate cannot overwrite a result');
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(get(state.results).close_tab, undefined);
    assert.equal(get(state.lastResult), null);
  } finally { state.reset(); }
});

test('reducer errors and missing acknowledgments show failure and allow retry', async () => {
  const state = createHelperActions(10, 100);
  try {
    state.send('close_tab', async () => { throw new Error('sharing off'); });
    await tick();
    assert.equal(actionLabel('close_tab', false, get(state.results).close_tab), "Couldn't close it");
    state.send('point', async () => {}, { x: 0.25, y: 0.75 });
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(get(state.results).point?.ok, false);
    assert.equal(Object.keys(get(state.pending)).length, 0);
    assert.ok(state.send('point', async () => {}, { x: 1, y: 0 }));
    state.reset();
    assert.deepEqual(get(state.pending), {});
    assert.deepEqual(get(state.results), {});
  } finally { state.reset(); }
});

test('controls explain connection and sharing state, with waiting and completion copy', () => {
  assert.equal(actionDisabledReason(false, true), 'Wait until the call is connected');
  assert.equal(actionDisabledReason(true, false), 'Screen sharing is off');
  assert.equal(actionDisabledReason(true, true), '');
  assert.equal(actionLabel('close_tab', true), 'Closing\u2026');
  assert.equal(actionLabel('point', false), 'Point');
  assert.equal(actionLabel('bigger', false), 'Make it bigger');
});

function fakeWindow() {
  const listeners = new Set<(event: MessageEvent) => void>();
  const sent: unknown[] = [];
  const target = { location: { origin: 'https://calls.test' },
    addEventListener(_type: string, fn: (event: MessageEvent) => void) { listeners.add(fn); },
    removeEventListener(_type: string, fn: (event: MessageEvent) => void) { listeners.delete(fn); },
    postMessage(data: unknown, origin: string) { assert.equal(origin, target.location.origin); sent.push(data); }
  } as unknown as Window;
  function emit(data: unknown, origin = target.location.origin, source: unknown = target) {
    for (const fn of [...listeners]) fn({ data, origin, source } as MessageEvent);
  }
  return { target, sent, listeners, emit };
}

test('elder relay accepts only same-window, same-origin, boolean replies for the pending seq', async () => {
  const f = fakeWindow(), reports: unknown[] = [];
  const relay = createElderRelay(f.target, async (...args) => { reports.push(args); }, 500);
  try {
    relay.handle(action); relay.handle(action);
    assert.equal(f.sent.length, 1);
    assert.deepEqual(f.sent[0], { source: 'ally-call', type: 'helperAction', ...action });
    const reply = { source: 'ally-ext', type: 'helperActionResult', seq: 1, ok: true };
    f.emit(reply, 'https://evil.test'); f.emit(reply, undefined, {});
    f.emit({ ...reply, seq: 2 }); f.emit({ ...reply, ok: 'true' }); f.emit({ ...reply, source: 'evil' });
    await tick(); assert.equal(reports.length, 0);
    f.emit(reply); await tick();
    assert.deepEqual(reports, [[1, true]]);
    assert.equal(f.listeners.size, 0);
    relay.handle(action); assert.equal(f.sent.length, 1, 'events cannot execute twice');
  } finally { relay.stop(); }
});

test('elder relay reports false on extension timeout and removes listeners on teardown', async () => {
  const f = fakeWindow(), reports: unknown[] = [];
  const relay = createElderRelay(f.target, async (...args) => { reports.push(args); }, 10);
  relay.handle(action);
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.deepEqual(reports, [[1, false]]);
  assert.equal(f.listeners.size, 0);
  relay.handle({ ...action, seq: 2 }); relay.stop();
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.deepEqual(reports, [[1, false]]);
  assert.equal(f.listeners.size, 0);
});

test('mirrored event bindings preserve server column types and order', () => {
  for (const [binding, server] of [[HelperActionRow, module.default.schemaType.tables.helperActionEvent.rowType],
    [ActionResultRow, module.default.schemaType.tables.actionResultEvent.rowType]] as const) {
    const client = binding.algebraicType;
    if (client.tag !== 'Product') throw new Error('Expected row schema');
    assert.deepEqual(client.value.elements.map(column => column.algebraicType),
      server.elements.map(column => column.algebraicType));
  }
});
