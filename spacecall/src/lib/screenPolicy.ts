export type ScreenZoom = 1 | 2;

export function toggleScreenZoom(zoom: ScreenZoom): ScreenZoom {
  return zoom === 1 ? 2 : 1;
}

export function fitScreen(availableWidth: number, availableHeight: number, width: number, height: number) {
  if (![availableWidth, availableHeight, width, height].every(value => Number.isFinite(value) && value > 0)) {
    return { width: 0, height: 0 };
  }
  const scale = Math.min(availableWidth / width, availableHeight / height);
  return { width: width * scale, height: height * scale };
}

export function screenFreshness(updatedAt: number | null, now: number): string {
  if (updatedAt === null) return 'Waiting for the screen';
  if (now - updatedAt <= 2000) return '';
  const seconds = Math.max(0, Math.floor((now - updatedAt) / 1000));
  return `Screen updated ${seconds} s ago`;
}
