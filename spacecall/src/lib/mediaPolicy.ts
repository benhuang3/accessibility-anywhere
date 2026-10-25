export const HEALTH_WINDOW_MS = 5000;
export const AUDIO_CHUNK_MS = 40;
export const AUDIO_SAMPLES = 16000 * AUDIO_CHUNK_MS / 1000;
export const AUDIO_TARGET_MS = 120;
export const AUDIO_CAP_MS = 300;
export const MAX_AUDIO_PENDING = 6;

export type MetricKind = 'videoUp' | 'videoDown' | 'audioUp' | 'audioDown' | 'sendDrop' | 'videoSkip' | 'underrun';
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
    this.events = this.events.filter(event => event.at > now - HEALTH_WINDOW_MS);
  }

  sample(now: number, pending: number, oldestPendingMs: number, playingAudio: boolean) {
    this.prune(now);
    const seconds = Math.max(0.001, Math.min(HEALTH_WINDOW_MS, now - this.startedAt) / 1000);
    const count = (kind: MetricKind) => this.events.filter(event => event.kind === kind).length;
    const bytes = (kinds: MetricKind[]) => this.events.reduce((sum, event) =>
      sum + (kinds.includes(event.kind) ? event.bytes : 0), 0);
    const videoDown = count('videoDown') / seconds;
    const ready = now - this.startedAt >= HEALTH_WINDOW_MS;
    const bad = videoDown < 2 ||
      (playingAudio && (count('underrun') >= 3 || now - this.lastAudioAt >= 1000)) ||
      count('sendDrop') >= 3 || (count('videoSkip') >= 3 && count('videoUp') / seconds < 2) ||
      pending > MAX_AUDIO_PENDING || oldestPendingMs > 500;
    this.chip = advanceChip(this.chip, bad, now);
    return {
      videoUp: count('videoUp') / seconds, videoDown,
      audioUp: count('audioUp') / seconds, audioDown: count('audioDown') / seconds,
      kbpsUp: bytes(['videoUp', 'audioUp']) * 8 / seconds / 1000,
      kbpsDown: bytes(['videoDown', 'audioDown']) * 8 / seconds / 1000,
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
  pending = false;
  since = 0;

  async run(now: number, captureAndSend: () => Promise<void>) {
    if (this.pending) return false;
    this.pending = true;
    this.since = now;
    try { await captureAndSend(); } finally { this.pending = false; }
    return true;
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
