export type MainView = 'screen' | 'face';

export function swapMainView(view: MainView): MainView {
  return view === 'screen' ? 'face' : 'screen';
}

export function screenFreshness(updatedAt: number | null, now: number): string {
  if (updatedAt === null) return 'Waiting for the screen';
  const seconds = Math.max(0, Math.floor((now - updatedAt) / 1000));
  return `Screen updated ${seconds} s ago`;
}
