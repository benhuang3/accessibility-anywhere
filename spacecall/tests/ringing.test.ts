import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import { elderCallPresentation, noAnswerNote, playJoinChime } from '../src/lib/ringing.ts';

test('elder status maps to the dataset value and a matching changing title', () => {
  for (const status of [undefined, 'open', 'responding']) {
    assert.deepEqual(elderCallPresentation(status, 'Priya'), {
      state: 'ringing', label: 'Calling Priya\u2026', title: 'Calling Priya\u2026 \u00b7 Ally Call'
    });
  }
  assert.deepEqual(elderCallPresentation('connected', 'Amira'), {
    state: 'connected', label: 'Amira is here', title: 'Amira is here \u00b7 Ally Call'
  });
  for (const presentation of [elderCallPresentation('ended', 'Priya'), elderCallPresentation('connected', 'Priya', true)]) {
    assert.deepEqual(presentation, { state: 'ended', label: 'Call ended', title: 'Call ended \u00b7 Ally Call' });
  }
});

test('no-answer note starts at sixty seconds from opening, only while ringing', () => {
  assert.equal(noAnswerNote('ringing', 'Priya', 5000, 64999), '');
  assert.equal(noAnswerNote('ringing', 'Priya', 5000, 65000), "Priya hasn't answered yet. Ally sent her a message.");
  assert.equal(noAnswerNote('ringing', 'Amira', 5000, 180000), "Amira hasn't answered yet. Ally sent her a message.");
  assert.equal(noAnswerNote('connected', 'Priya', 5000, 65000), '');
  assert.equal(noAnswerNote('ended', 'Priya', 5000, 65000), '');
});

type Bounds = { left: number; top: number; width: number; height: number };
const source = readFileSync(new URL('../../extension/background.js', import.meta.url), 'utf8');
const geometry = runInNewContext(source.slice(source.indexOf('function callWindowGeometry('),
  source.indexOf('const ACTIONS = {')) + '\ncallWindowGeometry;') as (area: Bounds, unused?: boolean) => Bounds;
const plain = (value: Bounds): Bounds => JSON.parse(JSON.stringify(value));

test('mini-player geometry stays 380 by 360 with a sixteen-pixel work-area inset', () => {
  for (const connected of [false, true]) {
    assert.deepEqual(plain(geometry({ left: 0, top: 0, width: 1600, height: 900 }, connected)),
      { left: 1204, top: 524, width: 380, height: 360 });
    assert.deepEqual(plain(geometry({ left: -1920, top: -100, width: 1920, height: 1040 }, connected)),
      { left: -396, top: 564, width: 380, height: 360 });
  }
});

function audioFixture(state = 'running') {
  const tones: { type: string; frequency: { value: number }; starts: number[]; stops: number[]; onended?: () => void }[] = [];
  const envelopes: number[][][] = [];
  let closes = 0;
  const context = {
    state, currentTime: 10, destination: {},
    close: async () => { closes++; },
    createOscillator() {
      const tone = { type: '', frequency: { value: 0 }, starts: [] as number[], stops: [] as number[],
        connect() {}, start(time: number) { this.starts.push(time); }, stop(time: number) { this.stops.push(time); } };
      tones.push(tone);
      return tone;
    },
    createGain() {
      const envelope: number[][] = [];
      envelopes.push(envelope);
      return { connect() {}, gain: {
        setValueAtTime: (value: number, time: number) => envelope.push([value, time]),
        linearRampToValueAtTime: (value: number, time: number) => envelope.push([value, time])
      } };
    }
  };
  return { context: context as unknown as AudioContext, tones, envelopes, closes: () => closes };
}

test('join chime schedules two quiet notes under a second and closes its context', () => {
  const audio = audioFixture();
  playJoinChime(() => audio.context);
  assert.deepEqual(audio.tones.map(tone => tone.frequency.value), [523.25, 659.25]);
  assert.equal(audio.tones.length, 2);
  assert.ok(audio.tones.every(tone => tone.type === 'sine'));
  assert.ok(audio.tones[1].stops[0] - 10 < 1);
  assert.ok(audio.tones[1].starts[0] > audio.tones[0].stops[0]);
  for (const envelope of audio.envelopes) assert.deepEqual(envelope.map(point => point[0]), [0, 0.035, 0]);
  audio.tones[1].onended!();
  assert.equal(audio.closes(), 1);
});

test('blocked or unavailable Web Audio silently skips the join chime', () => {
  const audio = audioFixture('suspended');
  playJoinChime(() => audio.context);
  assert.equal(audio.tones.length, 0);
  assert.equal(audio.closes(), 1);
  assert.doesNotThrow(() => playJoinChime(() => { throw new Error('Audio unavailable'); }));
});
