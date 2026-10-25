import { get, writable } from 'svelte/store';
import workletUrl from './pcm-capture-worklet.ts?worker&url';
import type { DbConnection } from '../module_bindings';
import type { AudioFrame, VideoFrame } from './types';
import { decodeJpeg } from './jpeg';
import {
  AUDIO_CHUNK_MS, AUDIO_SAMPLES, MAX_AUDIO_PENDING, AudioJitterBuffer, CallHealth, CameraPolicy, LatestFrameSender, LatestFrameReceiver, audioStartTime, audioScheduleDue, serverDelayMs
} from './mediaPolicy';

export const localVideoStream = writable<MediaStream | null>(null);
export const remoteVideoUrl = writable<string | null>(null);
export const connectionSlow = writable(false);
export const muted = writable(false);
export const audioNeedsTap = writable(false);
export const mediaError = writable('');
const emptyStats = {
  videoUp: 0, videoDown: 0, audioUp: 0, audioDown: 0, kbpsUp: 0, kbpsDown: 0,
  screenDown: 0, screenKbps: 0, callBytesPerSecond: 0, cameraTarget: 12,
  playbackMs: 0, pending: 0, delayMs: null as number | null
};
export const callStats = writable({ ...emptyStats });

let audioContext: AudioContext | null = null;
let runtime: Runtime | null = null;
let generation = 0;

type Runtime = {
  conn: DbConnection; roomId: string; stream: MediaStream; context: AudioContext;
  video: HTMLVideoElement; node: AudioWorkletNode; source: MediaStreamAudioSourceNode;
  silent: GainNode; timer: number; watchdog: number; playbackTimer: number; sending: boolean; stopped: boolean;
  audioPending: Map<number, number>; otherPending: number; lastAudioSend: number; videoSeq: number; audioSeq: number;
  nextPlay: number; lastAudioSeq: number;
  health: CallHealth; videoSender: LatestFrameSender; jitter: AudioJitterBuffer<AudioBuffer>;
  cameraPolicy: CameraPolicy; videoReceiver: LatestFrameReceiver<Uint8Array, string>;
  facing: 'user' | 'environment'; delayMs: number | null;
  sources: Set<AudioBufferSourceNode>;
};

// Call synchronously inside the helper's tap, before any network or permission await.
export function resumeCallAudio(): Promise<void> {
  if (!audioContext || audioContext.state === 'closed') audioContext = new AudioContext();
  const context = audioContext;
  audioNeedsTap.set(context.state !== 'running');
  context.onstatechange = () => audioNeedsTap.set(context.state !== 'running' && context.state !== 'closed');
  return context.resume().then(() => audioNeedsTap.set(context.state !== 'running')).catch(() => {
    audioNeedsTap.set(true);
  });
}

export async function startCallRuntime(conn: DbConnection, roomId: string) {
  const ticket = ++generation;
  mediaError.set('');
  void resumeCallAudio();
  const context = audioContext!;
  let stream: MediaStream | undefined;
  try {
    if (!navigator.mediaDevices?.getUserMedia || !context.audioWorklet) {
      throw new Error('Please open this call in Safari or Chrome using a secure link.');
    }
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: { facingMode: 'user', width: { ideal: 480 }, height: { ideal: 270 }, frameRate: { ideal: 15, max: 15 } }
    });
    if (ticket !== generation) { stream.getTracks().forEach(track => track.stop()); return; }
    await context.audioWorklet.addModule(workletUrl);
    if (ticket !== generation) { stream.getTracks().forEach(track => track.stop()); return; }
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.autoplay = true;
    video.srcObject = stream;
    await video.play();
    if (ticket !== generation) { stream.getTracks().forEach(track => track.stop()); video.srcObject = null; return; }
    const source = context.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(context, 'pcm-capture');
    const silent = context.createGain();
    silent.gain.value = 0;
    // Keep the worklet graph active, without playing the microphone locally.
    source.connect(node).connect(silent).connect(context.destination);
    const rt: Runtime = {
      conn, roomId, stream, context, video, source, node, silent, timer: 0, watchdog: 0, playbackTimer: 0,
      sending: false, stopped: false, audioPending: new Map(), otherPending: 0, lastAudioSend: 0,
      videoSeq: 0, audioSeq: 0, nextPlay: 0, lastAudioSeq: -1,
      health: new CallHealth(performance.now()), videoSender: new LatestFrameSender(),
      jitter: new AudioJitterBuffer(), cameraPolicy: new CameraPolicy(),
      videoReceiver: new LatestFrameReceiver(decodeJpeg, url => {
        const old = get(remoteVideoUrl);
        remoteVideoUrl.set(url);
        if (old) URL.revokeObjectURL(old);
      }, url => URL.revokeObjectURL(url)),
      facing: 'user', sources: new Set(), delayMs: null
    };
    runtime = rt;
    stream.getTracks().forEach(track => track.addEventListener('ended', () => {
      if (!rt.stopped) mediaError.set('Your camera or microphone stopped. Please open a new call from Ally.');
    }));
    muted.set(false);
    localVideoStream.set(stream);
    captureAudio(rt);
    captureVideo(rt);
    rt.playbackTimer = window.setInterval(() => playAudio(rt), 20);
    rt.watchdog = window.setInterval(() => {
      if (!rt.sending) return;
      const now = performance.now();
      const starts = [...rt.audioPending.values()];
      if (rt.videoSender.pending) starts.push(rt.videoSender.since);
      const pending = rt.audioPending.size + rt.videoSender.pending + rt.otherPending;
      const health = rt.health.sample(now, rt.audioPending.size + rt.videoSender.pending, starts.length ? now - Math.min(...starts) : 0,
        rt.context.state === 'running');
      const camera = rt.cameraPolicy.sample(now, health, rt.videoSender.congested(now));
      connectionSlow.set(health.slow);
      callStats.set({ ...health, cameraTarget: camera.fps, playbackMs: rt.jitter.bufferedMs + scheduledAudioMs(rt), pending, delayMs: rt.delayMs });
    }, 250);
  } catch (error) {
    stream?.getTracks().forEach(track => track.stop());
    if (ticket !== generation) return;
    stopCallRuntime();
    mediaError.set(error instanceof DOMException && ['NotAllowedError', 'PermissionDeniedError'].includes(error.name)
      ? 'Allow camera and microphone in your browser so you can see and hear each other.'
      : 'Your camera or microphone is unavailable. Close other calling apps and open this link again.');
    throw error;
  }
}

export function setTransmitting(enabled: boolean) {
  const rt = runtime;
  if (!rt) return;
  if (enabled && !rt.sending) {
    rt.health = new CallHealth(performance.now());
    rt.cameraPolicy = new CameraPolicy();
    connectionSlow.set(false);
  }
  rt.sending = enabled;
  if (!enabled) {
    rt.videoReceiver.clear();
    rt.jitter.clear();
    rt.sources.forEach(source => { try { source.stop(); } catch {} });
    rt.nextPlay = 0;
    connectionSlow.set(false);
  }
}

function captureAudio(rt: Runtime) {
  const size = Math.round(rt.context.sampleRate * AUDIO_CHUNK_MS / 1000);
  const input = new Float32Array(size);
  let used = 0;
  rt.node.port.onmessage = (event: MessageEvent<Float32Array>) => {
    if (rt.stopped) return;
    for (const sample of event.data) {
      input[used++] = sample;
      if (used !== size) continue;
      used = 0;
      if (!rt.sending) continue;
      const now = performance.now();
      // Bound the in-flight queue; late audio is dropped instead of accumulating latency.
      if (rt.audioPending.size >= MAX_AUDIO_PENDING || now - rt.lastAudioSend < 25) {
        rt.health.record('sendDrop', now);
        continue;
      }
      rt.lastAudioSend = now;
      const bytes = new Uint8Array(AUDIO_SAMPLES * 2);
      const view = new DataView(bytes.buffer);
      let sum = 0;
      for (let i = 0; i < AUDIO_SAMPLES; i++) {
        const position = i * input.length / AUDIO_SAMPLES;
        const left = Math.floor(position);
        const fraction = position - left;
        const value = get(muted) ? 0 : Math.max(-1, Math.min(1,
          input[left] * (1 - fraction) + input[Math.min(left + 1, input.length - 1)] * fraction));
        sum += value * value;
        view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
      }
      const seq = rt.audioSeq++ >>> 0;
      rt.audioPending.set(seq, now);
      rt.health.record('audioUp', now, bytes.length);
      void rt.conn.reducers.sendAudioFrame({
        roomId: rt.roomId, seq, sampleRate: 16000, channels: 1,
        rms: Math.sqrt(sum / AUDIO_SAMPLES), pcm16Le: bytes
      }).catch(() => {
        if (!rt.stopped) rt.health.record('sendDrop', performance.now());
      }).finally(() => { rt.audioPending.delete(seq); });
    }
  };
}

function captureVideo(rt: Runtime) {
  // A deadline timer keeps capture cadence independent of reducer acknowledgments.
  // Only encoding is single-flight; reducer acknowledgments can overlap three deep.
  let canvas: HTMLCanvasElement | OffscreenCanvas = document.createElement('canvas');
  try {
    if (typeof OffscreenCanvas !== 'undefined' && typeof OffscreenCanvas.prototype.convertToBlob === 'function') {
      const offscreen = new OffscreenCanvas(320, 180);
      if (offscreen.getContext('2d')) canvas = offscreen;
    }
  } catch { /* Older Safari needs the DOM canvas. */ }
  let graphics = canvas.getContext('2d') as CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
  if (!graphics) { mediaError.set('Your browser could not start video. Please open the link again.'); return; }
  let encoding = false;
  let nextCapture = performance.now();
  const tick = () => {
    if (rt.stopped) return;
    const start = performance.now();
    const settings = rt.cameraPolicy.settings;
    const budget = 1000 / settings.fps;
    if (rt.sending && rt.video.readyState >= 2 && !encoding) {
      // Check before capture/encoding; a busy sender retains no stale frame.
      void rt.videoSender.run(start, async () => {
        encoding = true;
        const seq = rt.videoSeq++ >>> 0;
        const { width: frameWidth, height: frameHeight } = settings;
        let jpeg: Uint8Array;
        try {
          canvas.width = frameWidth;
          canvas.height = frameHeight;
          const scale = Math.max(canvas.width / rt.video.videoWidth, canvas.height / rt.video.videoHeight);
          const width = rt.video.videoWidth * scale;
          const height = rt.video.videoHeight * scale;
          graphics!.drawImage(rt.video, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
          const surface = canvas;
          const blob = surface instanceof HTMLCanvasElement
            ? await new Promise<Blob | null>(resolve => surface.toBlob(resolve, 'image/jpeg', settings.quality))
            : await surface.convertToBlob({ type: 'image/jpeg', quality: settings.quality });
          if (rt.stopped) return;
          if (!blob || blob.size > 128000) throw new Error('Video frame too large');
          jpeg = new Uint8Array(await blob.arrayBuffer());
        } catch (error) {
          if (!(canvas instanceof HTMLCanvasElement)) {
            canvas = document.createElement('canvas');
            graphics = canvas.getContext('2d');
          }
          throw error;
        } finally { encoding = false; }
        if (rt.stopped || !rt.sending) return;
        rt.health.record('videoUp', performance.now(), jpeg.length);
        await rt.conn.reducers.sendVideoFrame({
          roomId: rt.roomId, seq, width: frameWidth, height: frameHeight, jpeg
        });
      }).then(sent => {
        if (rt.stopped) return;
        if (!sent) rt.health.record('videoSkip', start);
      }).catch(() => {
        if (!rt.stopped) rt.health.record('sendDrop', performance.now());
      });
    }
    nextCapture = Math.max(nextCapture + budget, performance.now() + 1);
    rt.timer = window.setTimeout(tick, nextCapture - performance.now());
  };
  tick();
}

export function toggleMute() {
  muted.update(value => !value);
  runtime?.stream.getAudioTracks().forEach(track => { track.enabled = !get(muted); });
}

export async function flipCamera() {
  const rt = runtime;
  if (!rt) return;
  const facing = rt.facing === 'user' ? 'environment' : 'user';
  // Safari often requires releasing the current camera before switching lenses.
  rt.stream.getVideoTracks().forEach(track => { track.stop(); rt.stream.removeTrack(track); });
  try {
    const camera = await navigator.mediaDevices.getUserMedia({
      audio: false, video: { facingMode: { ideal: facing }, width: { ideal: 480 }, height: { ideal: 270 }, frameRate: { ideal: 15, max: 15 } }
    });
    if (rt.stopped) { camera.getTracks().forEach(track => track.stop()); return; }
    camera.getVideoTracks().forEach(track => rt.stream.addTrack(track));
    rt.facing = facing;
    rt.video.srcObject = rt.stream;
    await rt.video.play();
    localVideoStream.set(new MediaStream(rt.stream.getTracks()));
    mediaError.set('');
  } catch {
    if (!rt.stopped) mediaError.set('The camera could not switch. You can still hear each other.');
  }
}

function scheduledAudioMs(rt: Runtime) {
  return Math.max(0, rt.nextPlay - rt.context.currentTime) * 1000;
}

function playAudio(rt: Runtime) {
  if (rt.stopped || !rt.sending) return;
  if (rt.context.state !== 'running') {
    rt.jitter.clear();
    rt.nextPlay = 0;
    return;
  }
  const now = rt.context.currentTime;
  if (rt.nextPlay && rt.nextPlay <= now && rt.jitter.underrun()) {
    rt.health.record('underrun', performance.now());
  }
  if (!audioScheduleDue(rt.nextPlay, now)) return;
  const buffer = rt.jitter.take();
  if (!buffer) return;
  const source = rt.context.createBufferSource();
  source.buffer = buffer;
  source.connect(rt.context.destination);
  rt.sources.add(source);
  source.onended = () => { rt.sources.delete(source); source.disconnect(); };
  const start = audioStartTime(rt.nextPlay, now);
  source.start(start);
  rt.nextPlay = start + buffer.duration;
}

export function handleAudioEvent(row: AudioFrame) {
  const rt = runtime;
  if (!rt || !rt.sending || row.roomId !== rt.roomId || row.seq <= rt.lastAudioSeq ||
      row.sampleRate !== 16000 || row.channels !== 1 || row.pcm16Le.length !== AUDIO_SAMPLES * 2) return;
  rt.lastAudioSeq = row.seq;
  rt.health.record('audioDown', performance.now(), row.pcm16Le.length);
  rt.delayMs = serverDelayMs(row, Date.now());
  if (rt.context.state !== 'running') { audioNeedsTap.set(true); return; }
  const buffer = rt.context.createBuffer(1, AUDIO_SAMPLES, 16000);
  const samples = buffer.getChannelData(0);
  const view = new DataView(row.pcm16Le.buffer, row.pcm16Le.byteOffset, row.pcm16Le.byteLength);
  for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
  rt.jitter.enqueue(buffer, scheduledAudioMs(rt));
  playAudio(rt);
}

export function handleVideoEvent(row: VideoFrame) {
  const rt = runtime;
  if (!rt || !rt.sending || row.roomId !== rt.roomId) return;
  rt.health.record('videoDown', performance.now(), row.jpeg.length);
  rt.delayMs = serverDelayMs(row, Date.now());
  rt.videoReceiver.push(row.seq, row.jpeg);
}

export function recordScreenFrame(bytes: number) {
  runtime?.health.record('screenDown', performance.now(), bytes);
}

export async function trackReducerCall<T>(call: () => Promise<T>): Promise<T> {
  const rt = runtime;
  if (rt) rt.otherPending++;
  try { return await call(); } finally { if (rt) rt.otherPending--; }
}

export function recordSendFailure() {
  runtime?.health.record('sendDrop', performance.now());
}

export function stopCallRuntime() {
  generation++;
  const rt = runtime;
  runtime = null;
  if (rt) {
    rt.stopped = true;
    clearTimeout(rt.timer);
    clearInterval(rt.watchdog);
    clearInterval(rt.playbackTimer);
    rt.jitter.clear();
    rt.videoReceiver.clear(true);
    rt.node.port.onmessage = null;
    rt.node.disconnect(); rt.source.disconnect(); rt.silent.disconnect();
    rt.stream.getTracks().forEach(track => track.stop());
    rt.sources.forEach(source => { try { source.stop(); } catch {} });
    rt.video.pause(); rt.video.srcObject = null;
  }
  if (audioContext) { audioContext.onstatechange = null; void audioContext.close().catch(() => {}); audioContext = null; }
  const url = get(remoteVideoUrl);
  if (url) URL.revokeObjectURL(url);
  localVideoStream.set(null);
  remoteVideoUrl.set(null);
  audioNeedsTap.set(false);
  connectionSlow.set(false);
  callStats.set({ ...emptyStats });
}
