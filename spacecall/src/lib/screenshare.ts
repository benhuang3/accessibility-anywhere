import { get, writable } from 'svelte/store';
import type { ScreenFrame } from './types';
import { LatestFrameReceiver } from './mediaPolicy';
import { decodeJpeg } from './jpeg';
import { recordScreenFrame } from './callRuntime';

export type SharedScreen = { url: string; updatedAt: number; width: number; height: number };
export const remoteScreen = writable<SharedScreen | null>(null);
// Screen frames paint as soon as decoding finishes, without a timer or fps cap.
const receiver = new LatestFrameReceiver<ScreenFrame, SharedScreen>(async row => ({
  url: await decodeJpeg(row.jpeg), updatedAt: Number(row.serverAt.microsSinceUnixEpoch) / 1000,
  width: row.width, height: row.height
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
