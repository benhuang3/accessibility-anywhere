import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import { isDifferentCall, parseRoomHash } from '../src/lib/room.ts';

const hash = '#room=abcdefghijklmnop&role=helper';
const nextHash = '#room=qrstuvwxyz012345&role=helper';
const current = parseRoomHash(hash);

test('a new room is a different call for either role', () => {
  for (const role of ['elder', 'helper']) {
    const room = parseRoomHash(hash.replace('helper', role));
    assert.equal(isDifferentCall(room, nextHash.replace('helper', role)), true);
  }
});

test('changing role in the same room is a different call in either direction', () => {
  const elderHash = hash.replace('helper', 'elder');
  assert.equal(isDifferentCall(current, elderHash), true);
  assert.equal(isDifferentCall(parseRoomHash(elderHash), hash), true);
});

test('a valid call replaces a missing current link', () => {
  assert.equal(isDifferentCall(null, hash), true);
});

test('the same room and role ignore debug and other metadata changes', () => {
  for (const sameCall of [hash, `${hash}&debug=1`, `${hash}&debug=0`,
    `${hash}&name=Priya&kind=remote_access&screen=off`, '#role=helper&room=abcdefghijklmnop']) {
    assert.equal(isDifferentCall(current, sameCall), false, sameCall);
  }
});

test('invalid hashes never replace a call, including when the room differs', () => {
  for (const invalid of ['', '#debug=1', '#room=short&role=helper', '#role=helper',
    '#room=qrstuvwxyz012345', nextHash.replace('helper', 'unknown'),
    `${nextHash}&kind=unknown`, `${nextHash}&name=`, `${nextHash}&name=%00`,
    `${nextHash}&name=${'x'.repeat(65)}`]) {
    assert.equal(isDifferentCall(current, invalid), false, invalid);
    assert.equal(isDifferentCall(null, invalid), false, invalid);
  }
});

test('hash changes reload a new call before debug updates and keep the listener cleanup', () => {
  const page = readFileSync(new URL('../src/routes/+page.svelte', import.meta.url), 'utf8');
  const start = page.indexOf('    const updateDebug =');
  const setup = page.slice(start, page.indexOf('    try {', start));
  let listener: (() => void) | undefined;
  let reloads = 0;
  let debugReads = 0;
  const location = { hash, reload() { reloads++; } };
  class DebugParams extends URLSearchParams {
    constructor(value: string) { super(value); debugReads++; }
  }
  const context = {
    link: current, isDifferentCall, URLSearchParams: DebugParams,
    window: { location, addEventListener(type: string, callback: () => void) {
      assert.equal(type, 'hashchange');
      listener = callback;
    }, removeEventListener(type: string, callback: () => void) {
      assert.equal(type, 'hashchange');
      assert.equal(callback, listener);
    } }
  };
  runInNewContext(setup, context);
  assert.ok(listener);
  assert.equal(reloads, 0, 'initial debug setup must not reload');
  assert.equal(debugReads, 1);
  location.hash = nextHash;
  listener();
  assert.equal(reloads, 1);
  assert.equal(debugReads, 1, 'reload must return before debug updates');
  location.hash = `${hash}&debug=1`;
  listener();
  assert.equal(reloads, 1);
  assert.equal(debugReads, 2);
  assert.equal(runInNewContext('debug', context), true);
  location.hash = '#invalid';
  listener();
  assert.equal(reloads, 1);
  assert.equal(debugReads, 3);
  const cleanup = page.match(/window\.removeEventListener\('hashchange', [^)]+\);/);
  assert.ok(cleanup);
  runInNewContext(cleanup[0], context);
});
