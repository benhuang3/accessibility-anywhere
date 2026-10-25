export type Role = 'elder' | 'helper';
export type RoomLink = { roomId: string; role: Role; name: string; kind: string };
export const ROOM_PATTERN = /^[A-Za-z0-9_-]{16,64}$/;
export const INCIDENT_TTL_MS = 2 * 60 * 60 * 1000;

export const kindLabels: Record<string, string> = {
  remote_access: 'a remote access app', scam_popup: 'a warning pop-up',
  lookalike_domain: 'a lookalike website', payment_form: 'a payment page',
  remote_banking: 'a banking page with remote access', browser_trap: 'a page that was hard to close',
  fake_scan: 'a fake computer scan', suspicious_page: 'a suspicious page', other: 'their computer'
};

export function parseRoomHash(hash: string): RoomLink {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const roomId = params.get('room') ?? '';
  const role = params.get('role');
  if (!ROOM_PATTERN.test(roomId) || (role !== 'elder' && role !== 'helper')) {
    throw new Error('This call link is incomplete. Please open the link Ally sent you.');
  }
  const kind = params.get('kind') ?? 'other';
  if (!Object.hasOwn(kindLabels, kind)) throw new Error('This call link has an unknown reason.');
  const name = (params.get('name') ?? 'your helper').trim();
  if (!name || name.length > 64 || /[\u0000-\u001f\u007f]/.test(name)) {
    throw new Error('This call link has an invalid helper name.');
  }
  return { roomId, role, name, kind };
}
