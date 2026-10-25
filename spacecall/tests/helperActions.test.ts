import assert from 'node:assert/strict';
import { test } from 'node:test';
import { get } from 'svelte/store';
import { actionKinds, actionDisabledReason, actionLabel, createHelperActions, createElderRelay,
  normalizedPoint, isHelperAction, type HelperAction } from '../src/lib/helperActions.ts';
import HelperActionRow from '../src/module_bindings/helper_action_event_table.ts';
import ActionResultRow from '../src/module_bindings/action_result_event_table.ts';
import * as module from '../server/src/index.ts';

const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const action: HelperAction = { seq: 1, action: 'close_tab', x: 0.25, y: 0.75 };

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

test('only close has pending state, completion feedback and expiring results', async () => {
  const state = createHelperActions(500, 10);
  try {
    const sent: HelperAction[] = [];
    const transmit = async (row: HelperAction) => { sent.push(row); };
    const close = state.send('close_tab', transmit)!;
    assert.equal(state.send('close_tab', transmit), null);
    state.send('hl_start', transmit, { x: .2, y: .8 });
    await tick();
    assert.equal(sent.length, 2);
    assert.deepEqual([close.x, close.y], [0, 0]);
    state.complete(999, true);
    assert.equal(Object.keys(get(state.pending)).length, 1);
    state.complete(close.seq, true);
    assert.deepEqual(get(state.lastResult), { seq: close.seq, action: 'close_tab', ok: true });
    assert.equal(actionLabel('close_tab', false, get(state.results).close_tab), 'Closed \u2713');
    state.complete(close.seq, false);
    assert.equal(get(state.results).close_tab?.ok, true);
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(get(state.results).close_tab, undefined);
    assert.equal(get(state.lastResult), null);
  } finally { state.reset(); }
});

test('moves send the latest point at most 20 times per second without awaiting reducers', context => {
  context.mock.timers.enable({ apis: ['setTimeout', 'Date'], now: 1000 });
  const state = createHelperActions();
  const sent: { row: HelperAction; at: number }[] = [];
  const transmit = (row: HelperAction) => {
    sent.push({ row, at: Date.now() });
    return new Promise(() => {});
  };
  try {
    state.send('hl_start', transmit);
    for (let i = 0; i < 200; i++) {
      state.send('hl_move', transmit, { x: i / 200, y: .5 });
      context.mock.timers.tick(1);
    }
    state.send('hl_end', transmit, { x: 1, y: .5 });
    const moves = sent.filter(event => event.row.action === 'hl_move');
    assert.equal(moves.length, 5);
    assert.ok(moves.every((event, i) => !i || event.at - moves[i - 1].at >= 50));
    assert.equal(moves[1].row.x, 49 / 200, 'older pending points are replaced');
    assert.equal(sent.at(-1)?.row.action, 'hl_end');
    context.mock.timers.tick(5000);
    assert.equal(sent.length, 7, 'no old moves are queued after the end');
    assert.deepEqual(get(state.pending), {});
    assert.deepEqual(get(state.results), {});
    state.send('touch_start', transmit);
    state.send('touch_move', transmit);
    state.send('touch_move', transmit);
    state.send('touch_end', transmit);
    const count = sent.length;
    context.mock.timers.tick(100);
    assert.equal(sent.length, count);
    state.send('touch_start', transmit);
    state.send('touch_move', transmit);
    state.send('touch_move', transmit);
    state.reset();
    const resetCount = sent.length;
    context.mock.timers.tick(100);
    assert.equal(sent.length, resetCount);
  } finally { state.reset(); }
});

test('failed streaming reducers keep going; close failures and timeouts permit retry', async () => {
  let now = 0;
  const state = createHelperActions(10, 100, () => now);
  try {
    const sent: string[] = [];
    for (const action of actionKinds.filter(action => action !== 'close_tab')) {
      now += 50;
      state.send(action, async () => { sent.push(action); throw Error('offline'); });
    }
    await tick();
    assert.equal(sent.length, 6);
    assert.deepEqual(get(state.results), {});
    state.send('close_tab', async () => { throw Error('sharing off'); });
    await tick();
    assert.equal(actionLabel('close_tab', false, get(state.results).close_tab), "Couldn't close it");
    state.send('close_tab', async () => {});
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(get(state.results).close_tab?.ok, false);
    assert.equal(Object.keys(get(state.pending)).length, 0);
    assert.ok(state.send('close_tab', async () => {}));
  } finally { state.reset(); }
});

test('controls explain connection and sharing state, with close feedback', () => {
  assert.equal(actionDisabledReason(false, true), 'Wait until the call is connected');
  assert.equal(actionDisabledReason(true, false), 'Screen sharing is off');
  assert.equal(actionDisabledReason(true, true), '');
  assert.equal(actionLabel('close_tab', true), 'Closing\u2026');
  assert.equal(actionLabel('close_tab', false), 'Close tab');
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

test('elder streams immediately without results or waiting for an unacknowledged close', async () => {
  const f = fakeWindow(), reports: unknown[] = [];
  const relay = createElderRelay(f.target, async (...args) => { reports.push(args); }, 500);
  try {
    relay.handle(action);
    for (const [i, kind] of actionKinds.filter(kind => kind !== 'close_tab').entries()) {
      const row = { ...action, seq: i + 2, action: kind };
      relay.handle(row); relay.handle(row);
    }
    assert.equal(f.sent.length, 7);
    assert.equal(f.listeners.size, 1, 'only close waits for a result');
    await tick();
    assert.equal(reports.length, 0);
  } finally { relay.stop(); }
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
