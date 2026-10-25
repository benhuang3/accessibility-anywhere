import { get, writable } from 'svelte/store';

export const actionKinds = ['close_tab', 'point', 'bigger'] as const;
export type HelperActionKind = typeof actionKinds[number];
export type Point = { x: number; y: number };
export type HelperAction = Point & { seq: number; action: HelperActionKind };
export type ActionResult = { seq: number; action: HelperActionKind; ok: boolean };
export type ActionResults = Partial<Record<HelperActionKind, ActionResult>>;

export function isHelperAction(value: unknown): value is HelperAction {
  if (!value || typeof value !== 'object') return false;
  const row = value as HelperAction;
  return Number.isInteger(row.seq) && row.seq >= 0 && row.seq <= 0xffffffff &&
    actionKinds.includes(row.action) && validPoint(row);
}

export function validPoint(point: Point): boolean {
  return Number.isFinite(point.x) && Number.isFinite(point.y) &&
    point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1;
}

// Use the image's displayed rect: it already includes fit, 2x zoom and viewport scrolling.
export function normalizedPoint(clientX: number, clientY: number,
  rect: { left: number; top: number; width: number; height: number }): Point | null {
  if (![clientX, clientY, rect.left, rect.top, rect.width, rect.height].every(Number.isFinite) ||
      rect.width <= 0 || rect.height <= 0) return null;
  const point = { x: (clientX - rect.left) / rect.width, y: (clientY - rect.top) / rect.height };
  return validPoint(point) ? point : null;
}

export function actionDisabledReason(connected: boolean, sharing: boolean): string {
  return !connected ? 'Wait until the call is connected' : !sharing ? 'Screen sharing is off' : '';
}

export function actionLabel(action: HelperActionKind, pending: boolean, result?: ActionResult): string {
  if (pending) return { close_tab: 'Closing\u2026', point: 'Pointing\u2026', bigger: 'Making bigger\u2026' }[action];
  if (result) return result.ok
    ? { close_tab: 'Closed \u2713', point: 'Pointed \u2713', bigger: 'Made bigger \u2713' }[action]
    : { close_tab: "Couldn't close it", point: "Couldn't point", bigger: "Couldn't make it bigger" }[action];
  return { close_tab: 'Close this page', point: 'Point', bigger: 'Make it bigger' }[action];
}

export function createHelperActions(timeoutMs = 8000, resultMs = 4000) {
  const pending = writable<Record<number, HelperAction>>({});
  const results = writable<ActionResults>({});
  const lastResult = writable<ActionResult | null>(null);
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const waits = new Map<number, ReturnType<typeof setTimeout>>();
  let seq = 0;
  function later(callback: () => void, ms: number) {
    const timer = setTimeout(() => { timers.delete(timer); callback(); }, ms);
    timers.add(timer);
    return timer;
  }
  function complete(sequence: number, ok: boolean) {
    const action = get(pending)[sequence];
    if (!action || typeof ok !== 'boolean') return;
    clearTimeout(waits.get(sequence));
    timers.delete(waits.get(sequence)!);
    waits.delete(sequence);
    pending.update(rows => { const next = { ...rows }; delete next[sequence]; return next; });
    const result = { seq: sequence, action: action.action, ok };
    results.update(rows => ({ ...rows, [action.action]: result }));
    lastResult.set(result);
    later(() => {
      results.update(rows => {
        if (rows[action.action] !== result) return rows;
        const next = { ...rows }; delete next[action.action]; return next;
      });
      if (get(lastResult) === result) lastResult.set(null);
    }, resultMs);
  }
  function send(action: HelperActionKind, transmit: (row: HelperAction) => Promise<unknown>,
    point: Point = { x: 0, y: 0 }): HelperAction | null {
    if (!actionKinds.includes(action) || !validPoint(point) ||
        Object.values(get(pending)).some(row => row.action === action)) return null;
    const row = { seq: seq = (seq + 1) >>> 0, action,
      x: action === 'point' ? point.x : 0, y: action === 'point' ? point.y : 0 };
    pending.update(rows => ({ ...rows, [row.seq]: row }));
    results.update(rows => { const next = { ...rows }; delete next[action]; return next; });
    waits.set(row.seq, later(() => complete(row.seq, false), timeoutMs));
    void Promise.resolve().then(() => transmit(row)).catch(() => complete(row.seq, false));
    return row;
  }
  function reset() {
    for (const timer of timers) clearTimeout(timer);
    timers.clear(); waits.clear();
    pending.set({}); results.set({}); lastResult.set(null);
  }
  return { pending, results, lastResult, send, complete, reset };
}

export const helperActions = createHelperActions();
export const incomingHelperAction = writable<HelperAction | null>(null);

export function createElderRelay(target: Window, report: (seq: number, ok: boolean) => Promise<unknown>,
  timeoutMs = 5000) {
  const waiting = new Map<number, (ok: boolean, reportResult?: boolean) => void>();
  const seen = new Set<number>();
  function handle(row: HelperAction) {
    if (!isHelperAction(row) || seen.has(row.seq)) return;
    seen.add(row.seq);
    if (seen.size > 256) seen.delete(seen.values().next().value!);
    const finish = (ok: boolean, reportResult = true) => {
      if (!waiting.delete(row.seq)) return;
      clearTimeout(timer);
      target.removeEventListener('message', receive);
      if (reportResult) void Promise.resolve().then(() => report(row.seq, ok)).catch(() => {});
    };
    const receive = (event: MessageEvent) => {
      const data = event.data;
      if (event.source === target && event.origin === target.location.origin &&
          data?.source === 'ally-ext' && data.type === 'helperActionResult' &&
          data.seq === row.seq && typeof data.ok === 'boolean') finish(data.ok);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    waiting.set(row.seq, finish);
    target.addEventListener('message', receive);
    target.postMessage({ source: 'ally-call', type: 'helperAction', seq: row.seq,
      action: row.action, x: row.x, y: row.y }, target.location.origin);
  }
  return { handle, stop() {
    for (const finish of [...waiting.values()]) finish(false, false);
    seen.clear();
  } };
}
