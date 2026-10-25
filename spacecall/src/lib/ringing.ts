export type ElderCallState = 'ringing' | 'connected' | 'ended';
export const NO_ANSWER_MS = 60_000;

export function elderCallPresentation(incidentStatus: string | undefined, name: string, ended = false) {
  const state: ElderCallState = ended || incidentStatus === 'ended' ? 'ended'
    : incidentStatus === 'connected' ? 'connected' : 'ringing';
  const label = state === 'ended' ? 'Call ended'
    : state === 'connected' ? `${name} is here` : `Calling ${name}\u2026`;
  return { state, label, title: `${label} \u00b7 Ally Call` };
}

export function noAnswerNote(state: ElderCallState, name: string, openedAt: number, now: number) {
  return state === 'ringing' && now - openedAt >= NO_ANSWER_MS
    ? `${name} hasn't answered yet. Ally sent her a message.` : '';
}

export function playJoinChime(createContext: () => AudioContext = () => new AudioContext()) {
  let context: AudioContext | undefined;
  const close = () => { void context?.close().catch(() => {}); };
  try {
    context = createContext();
    // Autoplay policy can suspend a fresh context. Do not queue a delayed sound.
    if (context.state !== 'running') { close(); return; }
    for (const [index, frequency] of [523.25, 659.25].entries()) {
      const start = context.currentTime + 0.01 + index * 0.23;
      const tone = context.createOscillator();
      const volume = context.createGain();
      tone.type = 'sine';
      tone.frequency.value = frequency;
      volume.gain.setValueAtTime(0, start);
      volume.gain.linearRampToValueAtTime(0.035, start + 0.02);
      volume.gain.linearRampToValueAtTime(0, start + 0.2);
      tone.connect(volume);
      volume.connect(context.destination);
      if (index === 1) tone.onended = close;
      tone.start(start);
      tone.stop(start + 0.2);
    }
  } catch { close(); }
}
