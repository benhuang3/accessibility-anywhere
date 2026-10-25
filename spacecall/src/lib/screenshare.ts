import { get, writable } from 'svelte/store';
import type { ScreenFrame } from './types';

export const remoteScreen = writable<{ url: string; updatedAt: number } | null>(null);
let lastSeq = -1;

export function handleScreenEvent(row: ScreenFrame) {
  if (row.seq <= lastSeq) return;
  lastSeq = row.seq;
  const old = get(remoteScreen);
  remoteScreen.set({
    url: URL.createObjectURL(new Blob([new Uint8Array(row.jpeg)], { type: 'image/jpeg' })),
    updatedAt: Number(row.serverAt.microsSinceUnixEpoch) / 1000
  });
  if (old) URL.revokeObjectURL(old.url);
}

export function clearScreen(resetSequence = false) {
  const old = get(remoteScreen);
  if (old) URL.revokeObjectURL(old.url);
  remoteScreen.set(null);
  if (resetSequence) lastSeq = -1;
}
