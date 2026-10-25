import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  advanceChip, AudioJitterBuffer, CallHealth, LatestFrameSender, audioStartTime, audioScheduleDue, serverDelayMs,
  AUDIO_CAP_MS, AUDIO_CHUNK_MS, AUDIO_TARGET_MS, AUDIO_SAMPLES
} from '../src/lib/mediaPolicy.ts';

test('chip requires five seconds of trouble and five seconds of recovery', () => {
  let state = { slow: false, badSince: null as number | null, goodSince: null as number | null };
  state = advanceChip(state, true, 0);
  state = advanceChip(state, true, 4999);
  assert.equal(state.slow, false);
  state = advanceChip(state, true, 5000);
  assert.equal(state.slow, true);
  state = advanceChip(state, false, 5100);
  state = advanceChip(state, false, 10099);
  assert.equal(state.slow, true);
  assert.equal(advanceChip(state, false, 10100).slow, false);
});

test('isolated trouble and interrupted recovery never flicker the chip', () => {
  let state = { slow: false, badSince: null as number | null, goodSince: null as number | null };
  for (let now = 0; now < 20000; now += 1000) {
    state = advanceChip(state, now % 2000 === 0, now);
    assert.equal(state.slow, false);
  }
  state = advanceChip(state, true, 20000);
  state = advanceChip(state, true, 25000);
  state = advanceChip(state, false, 26000);
  state = advanceChip(state, true, 30999);
  state = advanceChip(state, false, 31000);
  assert.equal(advanceChip(state, false, 35999).slow, true);
  assert.equal(advanceChip(state, false, 36000).slow, false);
});

function feed(health: CallHealth, now: number, fps = 5) {
  health.record('audioDown', now, 1600);
  health.record('audioUp', now, 1600);
  if (now % (1000 / fps) === 0) {
    health.record('videoDown', now, 1000);
    health.record('videoUp', now, 1000);
  }
}

test('healthy received media stays healthy even with 200ms reducer acknowledgments', () => {
  const health = new CallHealth(0);
  for (let now = 0; now <= 30000; now += 50) {
    feed(health, now);
    const sample = health.sample(now, 5, 200, true);
    assert.equal(sample.slow, false);
    if (now >= 10000) assert.equal(sample.highQuality, true);
  }
});

test('rates and payload bandwidth use the rolling five-second window', () => {
  const health = new CallHealth(0);
  for (let now = 0; now <= 10000; now += 50) feed(health, now);
  const sample = health.sample(10000, 0, 0, true);
  assert.equal(sample.videoDown, 5);
  assert.equal(sample.videoUp, 5);
  assert.equal(sample.audioDown, 20);
  assert.equal(sample.audioUp, 20);
  assert.equal(sample.kbpsDown, 296);
  assert.equal(sample.kbpsUp, 296);
  assert.equal(health.sample(15001, 0, 0, true).kbpsDown, 0);
});

test('one fps, including a complete receive stall, eventually warns and recovers', () => {
  for (const fps of [0, 1]) {
    const health = new CallHealth(0);
    let sample;
    for (let now = 0; now <= 15000; now += 50) {
      if (fps) feed(health, now, fps);
      sample = health.sample(now, 0, 0, true);
    }
    assert.equal(sample!.slow, true);
    for (let now = 15050; now <= 30000; now += 50) {
      feed(health, now);
      sample = health.sample(now, 0, 0, true);
    }
    assert.equal(sample!.slow, false);
    assert.equal(sample!.highQuality, true);
  }
});

test('exactly two received fps is healthy, with a startup grace window', () => {
  const health = new CallHealth(0);
  for (let now = 0; now <= 15000; now += 50) {
    feed(health, now, 2);
    const sample = health.sample(now, 0, 0, true);
    assert.equal(sample.bad, false);
    assert.equal(sample.slow, false);
  }
  assert.equal(new CallHealth(0).sample(4999, 99, 10000, true).slow, false);
});

test('continuous trouble from startup shows after five seconds, without a second grace period', () => {
  const health = new CallHealth(0);
  assert.equal(health.sample(0, 0, 0, true).slow, false);
  assert.equal(health.sample(4999, 0, 0, true).slow, false);
  assert.equal(health.sample(5000, 0, 0, true).slow, true);
});

test('skipping busy capture ticks is harmless while actual sent and received fps stay healthy', () => {
  const health = new CallHealth(0);
  for (let now = 0; now <= 15000; now += 50) {
    feed(health, now);
    if (now % 200 === 0) health.record('videoSkip', now);
    assert.equal(health.sample(now, 5, 200, true).slow, false);
  }
});

test('repeated underruns warn despite healthy video; suspended audio is excluded', () => {
  for (const playing of [true, false]) {
    const health = new CallHealth(0);
    let sample;
    for (let now = 0; now <= 15000; now += 50) {
      feed(health, now);
      if (now % 1000 === 0) health.record('underrun', now);
      sample = health.sample(now, 0, 0, playing);
    }
    assert.equal(sample!.slow, playing);
  }
});

test('a stalled audio stream warns even when video continues', () => {
  const health = new CallHealth(0);
  let sample;
  for (let now = 0; now <= 15000; now += 200) {
    health.record('videoDown', now);
    sample = health.sample(now, 0, 0, true);
  }
  assert.equal(sample!.slow, true);
});

test('queue depth, old pending calls and repeated skipped sends each detect congestion', () => {
  for (const reason of ['depth', 'age', 'drops']) {
    const health = new CallHealth(0);
    let sample;
    for (let now = 0; now <= 15000; now += 50) {
      feed(health, now);
      if (reason === 'drops' && now % 1000 === 0) health.record('sendDrop', now);
      sample = health.sample(now, reason === 'depth' ? 7 : 1, reason === 'age' ? 501 : 100, true);
    }
    assert.equal(sample!.slow, true, reason);
  }
});

test('quality steps down on bad health before the chip appears', () => {
  const health = new CallHealth(0);
  for (let now = 0; now <= 10000; now += 50) { feed(health, now); health.sample(now, 0, 0, true); }
  assert.equal(health.sample(10000, 0, 0, true).highQuality, true);
  const congested = health.sample(10001, 7, 100, true);
  assert.equal(congested.highQuality, false);
  assert.equal(congested.slow, false);
});

test('jitter buffer primes near 120ms and preserves continuous chunk order', () => {
  const queue = new AudioJitterBuffer<number>();
  queue.enqueue(1, 0); queue.enqueue(2, 0);
  assert.equal(queue.take(), undefined);
  queue.enqueue(3, 0);
  assert.ok(queue.bufferedMs >= AUDIO_TARGET_MS && queue.bufferedMs < AUDIO_TARGET_MS + AUDIO_CHUNK_MS);
  assert.equal(queue.take(), 1);
  assert.equal(queue.take(), 2);
  assert.equal(queue.take(), 3);
  assert.equal(queue.underrun(), true);
  assert.equal(queue.underrun(), false, 'one gap counts once');
  queue.enqueue(4, 0);
  assert.equal(queue.take(), undefined, 'rebuffer after starvation');
});

test('jitter buffer drops oldest audio past 300ms, retaining the newest chunks', () => {
  const queue = new AudioJitterBuffer<number>();
  const capacity = Math.floor(AUDIO_CAP_MS / AUDIO_CHUNK_MS);
  for (let i = 1; i <= capacity; i++) assert.equal(queue.enqueue(i, 0), 0);
  assert.equal(queue.bufferedMs, capacity * AUDIO_CHUNK_MS);
  const retained = Math.floor(AUDIO_TARGET_MS / AUDIO_CHUNK_MS);
  assert.equal(queue.enqueue(capacity + 1, 0), capacity + 1 - retained);
  assert.equal(queue.bufferedMs, retained * AUDIO_CHUNK_MS);
  assert.deepEqual(Array.from({ length: retained }, () => queue.take()),
    Array.from({ length: retained }, (_, i) => capacity + 2 - retained + i));
});

test('jitter cap includes already scheduled audio and stays bounded through bursts', () => {
  const queue = new AudioJitterBuffer<number>();
  for (let i = 0; i < 1000; i++) {
    queue.enqueue(i, 70);
    assert.ok(queue.bufferedMs + 70 <= AUDIO_CAP_MS);
  }
  queue.clear();
  assert.equal(queue.bufferedMs, 0);
  assert.equal(queue.take(), undefined);
  assert.equal(queue.underrun(), false);
});

test('playback joins consecutive chunks without adding a new scheduling gap', () => {
  assert.equal(audioStartTime(1.01, 1), 1.01);
  assert.equal(audioStartTime(0, 1), 1.02);
  assert.equal(audioStartTime(0.9, 1), 1.02);
});

test('regular arrivals and 20ms playback ticks maintain continuous, bounded audio', () => {
  const queue = new AudioJitterBuffer<number>();
  let nextPlay = 0;
  let underruns = 0;
  let previousEnd = 0;
  let played = 0;
  for (let ms = 0; ms <= 10000; ms += 10) {
    const now = ms / 1000;
    if (ms % AUDIO_CHUNK_MS === 0) queue.enqueue(ms, Math.max(0, nextPlay - now) * 1000);
    if (ms % 20 !== 0 && ms % AUDIO_CHUNK_MS !== 0) continue;
    if (nextPlay && nextPlay <= now && queue.underrun()) underruns++;
    if (!audioScheduleDue(nextPlay, now) || queue.take() === undefined) continue;
    const start = audioStartTime(nextPlay, now);
    if (played) assert.equal(start, previousEnd, 'adjacent chunks meet exactly');
    nextPlay = previousEnd = start + AUDIO_CHUNK_MS / 1000;
    played++;
    assert.ok(queue.bufferedMs + (nextPlay - now) * 1000 <= AUDIO_CAP_MS);
  }
  assert.ok(played > 240);
  assert.equal(underruns, 0);
});

test('a missed playback deadline counts even if a chunk is waiting on the main thread', () => {
  const queue = new AudioJitterBuffer<number>();
  for (let i = 0; i < 3; i++) queue.enqueue(i, 0);
  assert.equal(queue.take(), 0);
  assert.equal(queue.underrun(), true);
  assert.equal(queue.take(), undefined, 'the underrun restarts priming');
});

test('latest-frame-wins skips capture while a reducer is pending, then captures fresh media', async () => {
  const sender = new LatestFrameSender();
  const captured: number[] = [];
  let frame = 1;
  let finish!: () => void;
  const pending = sender.run(10, async () => {
    captured.push(frame);
    await new Promise<void>(resolve => { finish = resolve; });
  });
  assert.equal(sender.pending, true);
  for (frame = 2; frame <= 5; frame++) {
    assert.equal(await sender.run(20, async () => { captured.push(frame); }), false);
  }
  assert.deepEqual(captured, [1]);
  finish();
  assert.equal(await pending, true);
  assert.equal(sender.pending, false);
  await sender.run(30, async () => { captured.push(frame); });
  assert.deepEqual(captured, [1, 6]);
});

test('a failed capture releases the single-flight gate', async () => {
  const sender = new LatestFrameSender();
  await assert.rejects(sender.run(0, async () => { throw new Error('send failed'); }));
  assert.equal(sender.pending, false);
  assert.equal(await sender.run(1, async () => {}), true);
});

test('timestamp estimates convert microseconds and disclose absent metadata or clock skew', () => {
  assert.equal(serverDelayMs({ tag: 'Reducer', value: { timestamp: { microsSinceUnixEpoch: 1234500n } } }, 1300), 65.5);
  assert.equal(serverDelayMs({ tag: 'Transaction' }, 1300), null);
  assert.equal(serverDelayMs(undefined, 1300), null);
  assert.equal(serverDelayMs({ tag: 'Reducer', value: { timestamp: { microsSinceUnixEpoch: 1400000n } } }, 1300), -100);
});

test('capture policy is 640 samples / 40ms and row timestamps work without reducer metadata', () => {
  assert.equal(AUDIO_CHUNK_MS, 40);
  assert.equal(AUDIO_SAMPLES, 640);
  assert.equal(serverDelayMs({ serverAt: { microsSinceUnixEpoch: 1234500n } }, 1300), 65.5);
});
