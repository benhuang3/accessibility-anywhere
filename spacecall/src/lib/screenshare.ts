import { get, writable } from 'svelte/store';
import type { ScreenFrame } from './types';
import { LatestFrameReceiver } from './mediaPolicy';
import { decodeJpeg } from './jpeg';
import { recordScreenFrame } from './callRuntime';

export const remoteScreen = writable<{ url: string; updatedAt: number } | null>(null);
const receiver = new LatestFrameReceiver<ScreenFrame, { url: string; updatedAt: number }>(async row => ({
  url: await decodeJpeg(row.jpeg), updatedAt: Number(row.serverAt.microsSinceUnixEpoch) / 1000
}), frame => {
  const old = get(remoteScreen);
  remoteScreen.set(frame);
  if (old) URL.revokeObjectURL(old.url);
}, frame => URL.revokeObjectURL(frame.url));

export function handleScreenEvent(row: ScreenFrame) {
  recordScreenFrame(row.jpeg.length);
  receiver.push(row.seq, row);
}

export function clearScreen(resetSequence = false) {
  const old = get(remoteScreen);
  if (old) URL.revokeObjectURL(old.url);
  remoteScreen.set(null);
  receiver.clear(resetSequence);
}
