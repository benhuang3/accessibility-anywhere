import { get, writable } from 'svelte/store';
import workletUrl from './pcm-capture-worklet.ts?worker&url';
import type { DbConnection } from '../module_bindings';
import type { AudioFrame, VideoFrame } from './types';

export const localVideoStream = writable<MediaStream | null>(null);
export const remoteVideoUrl = writable<string | null>(null);
export const connectionSlow = writable(false);
export const muted = writable(false);
export const audioNeedsTap = writable(false);
export const mediaError = writable('');

let audioContext: AudioContext | null = null;
let runtime: Runtime | null = null;
let generation = 0;

type Runtime = {
  conn: DbConnection; roomId: string; stream: MediaStream; context: AudioContext;
  video: HTMLVideoElement; node: AudioWorkletNode; source: MediaStreamAudioSourceNode;
  silent: GainNode; timer: number; watchdog: number; sending: boolean; stopped: boolean;
  audioPending: number; lastAudioSend: number; videoSeq: number; audioSeq: number;
  nextPlay: number; lastAudioSeq: number; lastVideoSeq: number;
  lastAudioAt: number; lastVideoAt: number; lastSendOk: number;
  healthy: number; highQuality: boolean; facing: 'user' | 'environment';
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
      video: { facingMode: 'user', width: { ideal: 480 }, height: { ideal: 270 }, frameRate: { ideal: 8, max: 8 } }
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
      conn, roomId, stream, context, video, source, node, silent, timer: 0, watchdog: 0,
      sending: false, stopped: false, audioPending: 0, lastAudioSend: 0,
      videoSeq: 0, audioSeq: 0, nextPlay: 0, lastAudioSeq: -1, lastVideoSeq: -1,
      lastAudioAt: performance.now(), lastVideoAt: performance.now(), lastSendOk: performance.now(),
      healthy: 0, highQuality: false, facing: 'user', sources: new Set()
    };
    runtime = rt;
    stream.getTracks().forEach(track => track.addEventListener('ended', () => {
      if (!rt.stopped) mediaError.set('Your camera or microphone stopped. Please open a new call from Ally.');
    }));
    muted.set(false);
    localVideoStream.set(stream);
    captureAudio(rt);
    void captureVideo(rt);
    rt.watchdog = window.setInterval(() => {
      if (!rt.sending) return;
      const now = performance.now();
      if (now - rt.lastAudioAt > 3000 || now - rt.lastVideoAt > 5000 || now - rt.lastSendOk > 3000) {
        connectionSlow.set(true);
        rt.highQuality = false;
        rt.healthy = 0;
      }
    }, 1000);
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
    rt.lastAudioAt = rt.lastVideoAt = rt.lastSendOk = performance.now();
  }
  rt.sending = enabled;
}

function captureAudio(rt: Runtime) {
  const size = Math.round(rt.context.sampleRate * 0.05);
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
      if (rt.audioPending >= 8 || now - rt.lastAudioSend < 25) {
        connectionSlow.set(true);
        continue;
      }
      rt.lastAudioSend = now;
      const bytes = new Uint8Array(1600);
      const view = new DataView(bytes.buffer);
      let sum = 0;
      for (let i = 0; i < 800; i++) {
        const position = i * input.length / 800;
        const left = Math.floor(position);
        const fraction = position - left;
        const value = get(muted) ? 0 : Math.max(-1, Math.min(1,
          input[left] * (1 - fraction) + input[Math.min(left + 1, input.length - 1)] * fraction));
        sum += value * value;
        view.setInt16(i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
      }
      rt.audioPending++;
      void rt.conn.reducers.sendAudioFrame({
        roomId: rt.roomId, seq: rt.audioSeq++ >>> 0, sampleRate: 16000, channels: 1,
        rms: Math.sqrt(sum / 800), pcm16Le: bytes
      }).then(() => { rt.lastSendOk = performance.now(); }).catch(() => {
        if (!rt.stopped) connectionSlow.set(true);
      }).finally(() => { rt.audioPending--; });
    }
  };
}

async function captureVideo(rt: Runtime) {
  const canvas = document.createElement('canvas');
  const graphics = canvas.getContext('2d');
  if (!graphics) { mediaError.set('Your browser could not start video. Please open the link again.'); return; }
  while (!rt.stopped) {
    const start = performance.now();
    const fps = rt.highQuality ? 8 : 5;
    const budget = 1000 / fps;
    try {
      if (rt.sending && rt.video.readyState >= 2) {
        canvas.width = rt.highQuality ? 480 : 320;
        canvas.height = rt.highQuality ? 270 : 180;
        const scale = Math.max(canvas.width / rt.video.videoWidth, canvas.height / rt.video.videoHeight);
        const width = rt.video.videoWidth * scale;
        const height = rt.video.videoHeight * scale;
        graphics.drawImage(rt.video, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
        const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, 'image/jpeg', 0.55));
        if (rt.stopped) return;
        if (!blob || blob.size > 128000) throw new Error('Video frame too large');
        const jpeg = new Uint8Array(await blob.arrayBuffer());
        if (rt.stopped || !rt.sending) continue;
        await rt.conn.reducers.sendVideoFrame({
          roomId: rt.roomId, seq: rt.videoSeq++ >>> 0, width: canvas.width, height: canvas.height, jpeg
        });
        if (rt.stopped) return;
        const elapsed = performance.now() - start;
        rt.lastSendOk = performance.now();
        if (elapsed < budget * 0.8 && rt.audioPending < 4) {
          rt.healthy++;
          if (rt.healthy >= 30 && performance.now() - rt.lastAudioAt < 3000 && performance.now() - rt.lastVideoAt < 5000) {
            rt.highQuality = true;
            connectionSlow.set(false);
          }
        } else {
          rt.healthy = 0;
          rt.highQuality = false;
          connectionSlow.set(true);
        }
      }
    } catch {
      if (rt.stopped) return;
      rt.healthy = 0;
      rt.highQuality = false;
      connectionSlow.set(true);
    }
    if (rt.stopped) return;
    await new Promise<void>(resolve => { rt.timer = window.setTimeout(resolve, Math.max(10, budget - (performance.now() - start))); });
  }
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
      audio: false, video: { facingMode: { ideal: facing }, width: { ideal: 480 }, height: { ideal: 270 }, frameRate: { ideal: 8, max: 8 } }
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

export function handleAudioEvent(row: AudioFrame) {
  const rt = runtime;
  if (!rt || !rt.sending || row.roomId !== rt.roomId || row.seq <= rt.lastAudioSeq ||
      row.sampleRate !== 16000 || row.channels !== 1 || row.pcm16Le.length !== 1600) return;
  rt.lastAudioSeq = row.seq;
  rt.lastAudioAt = performance.now();
  if (rt.context.state !== 'running') { audioNeedsTap.set(true); return; }
  const buffer = rt.context.createBuffer(1, 800, 16000);
  const samples = buffer.getChannelData(0);
  const view = new DataView(row.pcm16Le.buffer, row.pcm16Le.byteOffset, row.pcm16Le.byteLength);
  for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
  if (rt.nextPlay > rt.context.currentTime + 0.3) { connectionSlow.set(true); return; }
  const source = rt.context.createBufferSource();
  source.buffer = buffer;
  source.connect(rt.context.destination);
  rt.sources.add(source);
  source.onended = () => { rt.sources.delete(source); source.disconnect(); };
  const start = Math.max(rt.nextPlay, rt.context.currentTime + 0.025);
  source.start(start);
  rt.nextPlay = start + 0.05;
}

export function handleVideoEvent(row: VideoFrame) {
  const rt = runtime;
  if (!rt || !rt.sending || row.roomId !== rt.roomId || row.seq <= rt.lastVideoSeq) return;
  rt.lastVideoSeq = row.seq;
  rt.lastVideoAt = performance.now();
  const old = get(remoteVideoUrl);
  remoteVideoUrl.set(URL.createObjectURL(new Blob([new Uint8Array(row.jpeg)], { type: 'image/jpeg' })));
  if (old) URL.revokeObjectURL(old);
}

export function stopCallRuntime() {
  generation++;
  const rt = runtime;
  runtime = null;
  if (rt) {
    rt.stopped = true;
    clearTimeout(rt.timer);
    clearInterval(rt.watchdog);
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
}
