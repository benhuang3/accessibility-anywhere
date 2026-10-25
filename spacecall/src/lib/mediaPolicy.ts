export const HEALTH_WINDOW_MS = 5000;
export const AUDIO_CHUNK_MS = 40;
export const AUDIO_SAMPLES = 16000 * AUDIO_CHUNK_MS / 1000;
export const AUDIO_TARGET_MS = 120;
export const AUDIO_CAP_MS = 300;
export const MAX_AUDIO_PENDING = 6;
export const MAX_VIDEO_PENDING = 4;
export const MAX_MEDIA_PENDING = MAX_AUDIO_PENDING + MAX_VIDEO_PENDING;

export type MetricKind = 'videoUp' | 'videoDown' | 'screenDown' | 'audioUp' | 'audioDown' | 'sendDrop' | 'videoSkip' | 'underrun';
type Metric = { at: number; kind: MetricKind; bytes: number };
export type ChipState = { slow: boolean; badSince: number | null; goodSince: number | null };

export function advanceChip(state: ChipState, bad: boolean, now: number): ChipState {
  const badSince = bad ? state.badSince ?? now : null;
  const goodSince = bad ? null : state.goodSince ?? now;
  return {
    slow: bad ? state.slow || now - badSince! >= HEALTH_WINDOW_MS
      : state.slow && now - goodSince! < HEALTH_WINDOW_MS,
    badSince, goodSince
  };
}

export class CallHealth {
  private events: Metric[] = [];
  private startedAt: number;
  private lastAudioAt: number;
  private chip: ChipState = { slow: false, badSince: null, goodSince: null };

  constructor(now: number) { this.startedAt = this.lastAudioAt = now; }

  record(kind: MetricKind, at: number, bytes = 0) {
    this.events.push({ kind, at, bytes });
    if (kind === 'audioDown') this.lastAudioAt = at;
    this.prune(at);
  }

  private prune(now: number) {
    this.events = this.events.filter(event => event.at > now - 10000);
  }

  sample(now: number, pending: number, oldestPendingMs: number, playingAudio: boolean) {
    this.prune(now);
    const seconds = Math.max(0.001, Math.min(HEALTH_WINDOW_MS, now - this.startedAt) / 1000);
    const recent = this.events.filter(event => event.at > now - HEALTH_WINDOW_MS);
    const count = (kind: MetricKind) => recent.filter(event => event.kind === kind).length;
    const bytes = (kinds: MetricKind[]) => recent.reduce((sum, event) =>
      sum + (kinds.includes(event.kind) ? event.bytes : 0), 0);
    const videoDown = count('videoDown') / seconds;
    const ready = now - this.startedAt >= HEALTH_WINDOW_MS;
    const rate = (kinds: MetricKind[]) => this.events.reduce((sum, event) =>
      sum + (kinds.includes(event.kind) ? event.bytes : 0), 0) / 10;
    const callBytesPerSecond = rate(['videoUp', 'videoDown', 'screenDown']);
    const bad = videoDown < 2 ||
      (playingAudio && (count('underrun') >= 3 || now - this.lastAudioAt >= 1000)) ||
      count('sendDrop') >= 3 || (count('videoSkip') >= 3 && count('videoUp') / seconds < 2) ||
      pending > MAX_MEDIA_PENDING || oldestPendingMs > 500;
    this.chip = advanceChip(this.chip, bad, now);
    return {
      videoUp: count('videoUp') / seconds, videoDown,
      screenDown: count('screenDown') / seconds,
      screenKbps: bytes(['screenDown']) * 8 / seconds / 1000,
      callBytesPerSecond,
      audioUp: count('audioUp') / seconds, audioDown: count('audioDown') / seconds,
      kbpsUp: bytes(['videoUp', 'audioUp']) * 8 / seconds / 1000,
      kbpsDown: bytes(['videoDown', 'audioDown', 'screenDown']) * 8 / seconds / 1000,
      slow: this.chip.slow, bad,
      highQuality: ready && !bad && !this.chip.slow && this.chip.goodSince !== null &&
        now - this.chip.goodSince >= HEALTH_WINDOW_MS
    };
  }
}

// Only unscheduled chunks live here. Playback schedules at most one chunk ahead,
// so old audio can still be discarded before it reaches the device.
export class AudioJitterBuffer<T> {
  private chunks: T[] = [];
  private primed = false;

  get bufferedMs() { return this.chunks.length * AUDIO_CHUNK_MS; }

  enqueue(chunk: T, scheduledMs: number) {
    this.chunks.push(chunk);
    let dropped = 0;
    if (this.bufferedMs + scheduledMs > AUDIO_CAP_MS) {
      while (this.chunks.length && this.bufferedMs + scheduledMs > AUDIO_TARGET_MS) {
        this.chunks.shift();
        dropped++;
      }
    }
    return dropped;
  }

  take(): T | undefined {
    if (!this.primed && this.bufferedMs < AUDIO_TARGET_MS) return undefined;
    this.primed = true;
    return this.chunks.shift();
  }

  underrun() {
    if (!this.primed) return false;
    this.primed = false;
    return true;
  }

  clear() { this.chunks = []; this.primed = false; }
}

export class LatestFrameSender {
  private flights = new Set<{ at: number }>();
  private fullSince: number | null = null;

  get pending() { return this.flights.size; }
  get since() { return Math.min(...[...this.flights].map(flight => flight.at)); }
  congested(now: number) { return this.fullSince !== null && now - this.fullSince > 1000; }

  async run(now: number, captureAndSend: () => Promise<void>) {
    if (this.pending >= MAX_VIDEO_PENDING) return false;
    const flight = { at: now };
    this.flights.add(flight);
    if (this.pending === MAX_VIDEO_PENDING) this.fullSince = now;
    try { await captureAndSend(); } finally {
      this.flights.delete(flight);
      this.fullSince = null;
    }
    return true;
  }
}

export class CameraPolicy {
  private level = 1;
  private goodSince: number | null = null;
  private lastDown = -Infinity;

  sample(now: number, health: { highQuality: boolean }, congested: boolean) {
    if (congested) {
      this.goodSince = null;
      if (now - this.lastDown >= 1000) {
        this.level = Math.max(0, this.level - 1);
        this.lastDown = now;
      }
    } else if (health.highQuality) {
      this.goodSince ??= now;
      if (now - this.goodSince >= HEALTH_WINDOW_MS) {
        this.level = Math.min(2, this.level + 1);
        this.goodSince = now;
      }
    } else this.goodSince = null;
    return this.settings;
  }

  get settings() {
    return this.level === 2 ? { fps: 24, width: 640, height: 360, quality: 0.6 }
      : this.level === 1 ? { fps: 15, width: 480, height: 270, quality: 0.6 }
      : { fps: 8, width: 320, height: 180, quality: 0.45 };
  }
}

// Decode one frame at a time, retaining only the newest waiting frame. A newer
// arrival invalidates an older decode before it can replace the displayed image.
export class LatestFrameReceiver<T, D> {
  private latestSeq = -1;
  private lastDrawn = -1;
  private waiting: { seq: number; frame: T } | null = null;
  private running = false;
  private generation = 0;
  private ready: { seq: number; decoded: D; generation: number } | null = null;
  private scheduled = false;

  private decode: (frame: T) => Promise<D>;
  private draw: (decoded: D) => void;
  private dispose: (decoded: D) => void;
  private schedule: (paint: () => void) => void;

  constructor(decode: (frame: T) => Promise<D>, draw: (decoded: D) => void, dispose: (decoded: D) => void,
    schedule: (paint: () => void) => void = paint => paint()) {
    this.decode = decode;
    this.draw = draw;
    this.dispose = dispose;
    this.schedule = schedule;
  }

  push(seq: number, frame: T) {
    if (seq <= this.latestSeq || seq <= this.lastDrawn) return false;
    this.latestSeq = seq;
    this.waiting = { seq, frame };
    void this.drain();
    return true;
  }

  private async drain() {
    if (this.running) return;
    this.running = true;
    try {
      while (this.waiting) {
        const { seq, frame } = this.waiting;
        this.waiting = null;
        const generation = this.generation;
        try {
          const decoded = await this.decode(frame);
          if (generation === this.generation && seq === this.latestSeq && seq > this.lastDrawn) {
            this.paint(seq, decoded, generation);
          } else this.dispose(decoded);
        } catch { /* A broken JPEG must not block newer frames. */ }
      }
    } finally { this.running = false; }
  }

  private paint(seq: number, decoded: D, generation: number) {
    if (this.ready) this.dispose(this.ready.decoded);
    this.ready = { seq, decoded, generation };
    if (this.scheduled) return;
    this.scheduled = true;
    this.schedule(() => {
      this.scheduled = false;
      const ready = this.ready;
      this.ready = null;
      if (!ready) return;
      if (ready.generation === this.generation && ready.seq === this.latestSeq && ready.seq > this.lastDrawn) {
        this.lastDrawn = ready.seq;
        this.draw(ready.decoded);
      } else this.dispose(ready.decoded);
    });
  }

  clear(resetSequence = false) {
    this.generation++;
    this.waiting = null;
    if (this.ready) this.dispose(this.ready.decoded);
    this.ready = null;
    if (resetSequence) this.latestSeq = this.lastDrawn = -1;
  }
}

export function audioScheduleDue(nextPlay: number, now: number) {
  // Floating point chunk boundaries must not defer a whole playback tick.
  return nextPlay <= now + 0.02 + 1e-6;
}

export function audioStartTime(nextPlay: number, now: number) {
  return nextPlay > now ? nextPlay : now + 0.02;
}

type TimestampEvent = { serverAt?: { microsSinceUnixEpoch: bigint }; tag?: string; value?: { timestamp?: { microsSinceUnixEpoch: bigint } } };

// Prefer the timestamp carried by the media row, including HTTP-origin events.
// This measures server-to-receiver age, with client/server clock uncertainty.
export function serverDelayMs(event: TimestampEvent | undefined, now: number): number | null {
  const micros = event?.serverAt?.microsSinceUnixEpoch ??
    (event?.tag === 'Reducer' ? event.value?.timestamp?.microsSinceUnixEpoch : undefined);
  return micros === undefined ? null : now - Number(micros) / 1000;
}
