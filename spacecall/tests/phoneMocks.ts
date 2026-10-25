import { writable } from 'svelte/store';
import { remoteScreen } from '../src/lib/screenshare';

const image = (svg: string) => `data:image/svg+xml,${encodeURIComponent(svg)}`;
const face = image('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#648b80"/><circle cx="320" cy="160" r="90" fill="#e7c9a9"/><text x="320" y="330" text-anchor="middle" font-size="36">Camera fixture</text></svg>');
const screen = image('<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1000"><rect width="1600" height="1000" fill="#dfe8f0"/><rect x="4" y="4" width="1592" height="992" fill="none" stroke="#2353aa" stroke-width="8"/><g fill="#132d4e" font-size="70"><text x="30" y="90">TOP LEFT</text><text x="1570" y="90" text-anchor="end">TOP RIGHT</text><text x="30" y="960">BOTTOM LEFT</text><text x="1570" y="960" text-anchor="end">BOTTOM RIGHT</text><text x="800" y="500" text-anchor="middle">The whole shared screen</text></g></svg>');

export const incidentStore = writable<{ status: string; helperName: string; screenSharing: boolean } | null>(null);
export const isConnected = writable(false);
export const connectionError = writable('');
export const remoteVideoUrl = writable<string | null>(null);
export const localVideoStream = writable<MediaStream | null>(null);
export const muted = writable(false);
export const connectionSlow = writable(false);
export const audioNeedsTap = writable(false);
export const mediaError = writable('');
export const callStats = writable({ videoUp: 24, videoDown: 24, audioUp: 25, audioDown: 25,
  kbpsUp: 100, kbpsDown: 100, screenDown: 24, screenKbps: 1000, cameraTarget: 24,
  callBytesPerSecond: 1000, playbackMs: 120, pending: 0, delayMs: 20 });

export async function startCallRuntime() { localVideoStream.set(new MediaStream()); }
export function stopCallRuntime() {}
export function setTransmitting() {}
export async function resumeCallAudio() {}
export function toggleMute() { muted.update(value => !value); }
export async function flipCamera() {}
export function recordScreenFrame() {}
export function disconnectStdb() {}

export async function connectStdb() {
  const connected = () => {
    incidentStore.set({ status: 'connected', helperName: 'Priya', screenSharing: true });
    remoteVideoUrl.set(face);
    remoteScreen.set({ url: screen, width: 1600, height: 1000, updatedAt: Date.now() });
  };
  isConnected.set(true);
  return { reducers: {
    async openIncident() { connected(); },
    async joinCall() { connected(); },
    async helperResponding() {},
    async endCall() { incidentStore.update(row => row && ({ ...row, status: 'ended' })); },
    async setScreenSharing({ on }: { on: boolean }) { incidentStore.update(row => row && ({ ...row, screenSharing: on })); }
  } };
}
