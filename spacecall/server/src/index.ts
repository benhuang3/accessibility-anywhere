import { schema, table, t, SenderError, type ReducerCtx } from 'spacetimedb/server';
import { profileTable } from './profile.ts';

const incident = table({ name: 'incident' }, {
  room_id: t.string().primaryKey(),
  kind: t.string(),
  status: t.string(),
  created_at: t.timestamp(),
  updated_at: t.timestamp(),
  helper_name: t.string(),
  screen_sharing: t.bool()
});

const presence = table({ name: 'presence' }, {
  slot: t.string().primaryKey(),
  room_id: t.string().index('btree'),
  identity: t.identity().index('btree'),
  connection_id: t.string(),
  role: t.string(),
  joined: t.bool(),
  updated_at: t.timestamp()
});

const audioFrameEvent = table({ name: 'audio_frame_event', public: true, event: true }, {
  room_id: t.string(),
  sender: t.identity(),
  recipient: t.identity(),
  seq: t.u32(),
  sample_rate: t.u32(),
  channels: t.u8(),
  rms: t.f32(),
  server_at: t.timestamp(),
  pcm16_le: t.byteArray()
});

const videoFrameEvent = table({ name: 'video_frame_event', public: true, event: true }, {
  room_id: t.string(),
  sender: t.identity(),
  recipient: t.identity(),
  seq: t.u32(),
  width: t.u16(),
  height: t.u16(),
  server_at: t.timestamp(),
  jpeg: t.byteArray()
});

const screenFrameEvent = table({ name: 'screen_frame_event', public: true, event: true }, {
  room_id: t.string(),
  recipient: t.identity(),
  seq: t.u32(),
  jpeg: t.byteArray(),
  width: t.u16(),
  height: t.u16(),
  server_at: t.timestamp()
});

const faceProfile = profileTable();
const spacetime = schema({ incident, presence, audioFrameEvent, videoFrameEvent, screenFrameEvent,
  face_profile: faceProfile });
export default spacetime;
type Context = ReducerCtx<typeof spacetime.schemaType>;

// Face profile is private by default. Its only read path is a sender-filtered view.
export const save_face_profile = spacetime.reducer({ profile_json: t.string() }, (ctx, { profile_json }) => {
  if (new TextEncoder().encode(profile_json).length > 64 * 1024) throw new SenderError('Profile exceeds 64 KB');
  let parsed: unknown;
  try { parsed = JSON.parse(profile_json); } catch { throw new SenderError('Invalid profile JSON'); }
  if (!parsed || typeof parsed !== 'object' || (parsed as {version?: unknown}).version !== 1) {
    throw new SenderError('Invalid profile');
  }
  const row = { owner: ctx.sender, profile_json, version: 1, updated_at: ctx.timestamp };
  if (ctx.db.face_profile.owner.find(ctx.sender)) ctx.db.face_profile.owner.update(row);
  else ctx.db.face_profile.insert(row);
});

export const delete_face_profile = spacetime.reducer({}, ctx => {
  if (ctx.db.face_profile.owner.find(ctx.sender)) ctx.db.face_profile.owner.delete(ctx.sender);
});

export const own_face_profile = spacetime.view({ name: 'own_face_profile', public: true },
  t.array(faceProfile.rowType), ctx => {
    const row = ctx.db.face_profile.owner.find(ctx.sender);
    return row ? [row] : [];
  });

const TTL_US = 2n * 60n * 60n * 1_000_000n;
const ROOM = /^[A-Za-z0-9_-]{16,64}$/;
const KINDS = new Set([
  'remote_access', 'scam_popup', 'lookalike_domain', 'payment_form',
  'remote_banking', 'browser_trap', 'fake_scan', 'suspicious_page', 'other'
]);

function validateRoom(roomId: string) {
  if (!ROOM.test(roomId)) throw new SenderError('Invalid room link');
}

function liveIncident(ctx: Context, roomId: string) {
  validateRoom(roomId);
  const row = ctx.db.incident.room_id.find(roomId);
  if (!row) throw new SenderError('This call is not available');
  if (ctx.timestamp.microsSinceUnixEpoch - row.created_at.microsSinceUnixEpoch >= TTL_US) {
    if (row.status !== 'ended') {
      ctx.db.incident.room_id.update({ ...row, status: 'ended', updated_at: ctx.timestamp });
    }
    // Return normally so the expiry update commits instead of rolling back.
    return undefined;
  }
  return row.status === 'ended' ? undefined : row;
}

function claimRole(ctx: Context, roomId: string, role: string, joined: boolean) {
  const slot = `${roomId}:${role}`;
  const old = ctx.db.presence.slot.find(slot);
  if (old && !old.identity.isEqual(ctx.sender)) throw new SenderError('That person is already here');
  const other = ctx.db.presence.slot.find(`${roomId}:${role === 'elder' ? 'helper' : 'elder'}`);
  if (other?.identity.isEqual(ctx.sender)) throw new SenderError('Use a separate device to join');
  const row = {
    slot, room_id: roomId, identity: ctx.sender,
    connection_id: ctx.connectionId?.toHexString() ?? '', role,
    joined: joined || (old?.joined ?? false), updated_at: ctx.timestamp
  };
  if (old) ctx.db.presence.slot.update(row);
  else ctx.db.presence.insert(row);
}

function member(ctx: Context, roomId: string) {
  const row = [...ctx.db.presence.room_id.filter(roomId)].find(p => p.identity.isEqual(ctx.sender));
  if (!row) throw new SenderError('Use your call link to join first');
  return row;
}

export const open_incident = spacetime.reducer({
  room_id: t.string(), kind: t.string(), helper_name: t.string()
}, (ctx, { room_id, kind, helper_name }) => {
  validateRoom(room_id);
  if (!KINDS.has(kind)) throw new SenderError('Choose an incident kind, never page text or a URL');
  const name = helper_name.trim();
  if (!name || name.length > 64 || /[\u0000-\u001f\u007f]/.test(name)) {
    throw new SenderError('Invalid helper name');
  }
  const old = ctx.db.incident.room_id.find(room_id);
  if (old) {
    const elder = ctx.db.presence.slot.find(`${room_id}:elder`);
    if (!elder?.identity.isEqual(ctx.sender)) throw new SenderError('This call is already open');
    if (!liveIncident(ctx, room_id)) return;
    claimRole(ctx, room_id, 'elder', false);
    return;
  }
  ctx.db.incident.insert({
    room_id, kind, status: 'ringing', created_at: ctx.timestamp,
    updated_at: ctx.timestamp, helper_name: name, screen_sharing: true
  });
  claimRole(ctx, room_id, 'elder', false);
});

export const helper_responding = spacetime.reducer({ room_id: t.string() }, (ctx, { room_id }) => {
  validateRoom(room_id);
  if (!ctx.db.incident.room_id.find(room_id)) throw new SenderError('This call is not available');
  // Link holders may see the ended state, including when they arrive after expiry.
  claimRole(ctx, room_id, 'helper', false);
  const row = liveIncident(ctx, room_id);
  if (!row) return;
  if (row.status !== 'connected') {
    ctx.db.incident.room_id.update({ ...row, status: 'responding', updated_at: ctx.timestamp });
  }
});

export const join_call = spacetime.reducer({ room_id: t.string(), role: t.string() }, (ctx, { room_id, role }) => {
  if (role !== 'elder' && role !== 'helper') throw new SenderError('Invalid call role');
  const row = liveIncident(ctx, room_id);
  if (!row) return;
  const me = member(ctx, room_id);
  if (me.role !== role) throw new SenderError('Invalid call role');
  claimRole(ctx, room_id, role, true);
  const both = [...ctx.db.presence.room_id.filter(room_id)].filter(p => p.joined).length === 2;
  ctx.db.incident.room_id.update({
    ...row, status: both ? 'connected' : row.status, updated_at: ctx.timestamp
  });
});

export const end_call = spacetime.reducer({ room_id: t.string() }, (ctx, { room_id }) => {
  const row = liveIncident(ctx, room_id);
  if (!row) return;
  member(ctx, room_id);
  ctx.db.incident.room_id.update({ ...row, status: 'ended', updated_at: ctx.timestamp });
});

export const heartbeat = spacetime.reducer({ room_id: t.string() }, (ctx, { room_id }) => {
  if (!liveIncident(ctx, room_id)) return;
  const me = member(ctx, room_id);
  ctx.db.presence.slot.update({ ...me, updated_at: ctx.timestamp });
});

export const client_disconnected = spacetime.clientDisconnected(ctx => {
  for (const me of ctx.db.presence.identity.filter(ctx.sender)) {
    if (me.connection_id !== (ctx.connectionId?.toHexString() ?? '')) continue;
    const row = ctx.db.incident.room_id.find(me.room_id);
    if (row && row.status !== 'ended') {
      ctx.db.incident.room_id.update({ ...row, status: 'ended', updated_at: ctx.timestamp });
    }
    ctx.db.presence.slot.update({ ...me, joined: false, updated_at: ctx.timestamp });
  }
});

function recipient(ctx: Context, roomId: string) {
  const row = liveIncident(ctx, roomId);
  if (!row || row.status !== 'connected') return undefined;
  const me = member(ctx, roomId);
  if (!me.joined) throw new SenderError('Join the call first');
  const peer = ctx.db.presence.slot.find(`${roomId}:${me.role === 'elder' ? 'helper' : 'elder'}`);
  return peer?.joined ? peer.identity : undefined;
}

export const send_audio_frame = spacetime.reducer({
  room_id: t.string(), seq: t.u32(), sample_rate: t.u32(), channels: t.u8(), rms: t.f32(), pcm16_le: t.byteArray()
}, (ctx, args) => {
  const peer = recipient(ctx, args.room_id);
  if (!peer) return;
  if (args.sample_rate !== 16000 || args.channels !== 1 || ![1280, 1600].includes(args.pcm16_le.length) ||
      !Number.isFinite(args.rms) || args.rms < 0 || args.rms > 1) {
    throw new SenderError('Invalid audio frame');
  }
  ctx.db.audioFrameEvent.insert({ ...args, sender: ctx.sender, recipient: peer, server_at: ctx.timestamp });
});

export const send_video_frame = spacetime.reducer({
  room_id: t.string(), seq: t.u32(), width: t.u16(), height: t.u16(), jpeg: t.byteArray()
}, (ctx, args) => {
  const peer = recipient(ctx, args.room_id);
  if (!peer) return;
  if (!((args.width === 320 && args.height === 180) || (args.width === 480 && args.height === 270)) ||
      args.jpeg.length < 4 || args.jpeg.length > 128000 || args.jpeg[0] !== 0xff || args.jpeg[1] !== 0xd8) {
    throw new SenderError('Invalid video frame');
  }
  ctx.db.videoFrameEvent.insert({ ...args, sender: ctx.sender, recipient: peer, server_at: ctx.timestamp });
});

export const call_incident = spacetime.view(
  { name: 'call_incident', public: true }, t.array(incident.rowType), ctx => {
    return [...ctx.db.presence.identity.filter(ctx.sender)].flatMap(me => {
      const row = ctx.db.incident.room_id.find(me.room_id);
      return row ? [row] : [];
    });
  }
);

// Event tables cannot be read by views in SDK 2.x. Enforce privacy on the server,
// even if a modified client requests every event without a room filter.
export const audio_visibility = spacetime.clientVisibilityFilter.sql(
  'SELECT * FROM audio_frame_event WHERE recipient = :sender'
);
export const video_visibility = spacetime.clientVisibilityFilter.sql(
  'SELECT * FROM video_frame_event WHERE recipient = :sender'
);

export const set_screen_sharing = spacetime.reducer({ room_id: t.string(), on: t.bool() }, (ctx, { room_id, on }) => {
  const row = liveIncident(ctx, room_id);
  if (!row) throw new SenderError('Call ended');
  if (member(ctx, room_id).role !== 'elder') throw new SenderError('Only the elder can change screen sharing');
  ctx.db.incident.room_id.update({ ...row, screen_sharing: on, updated_at: ctx.timestamp });
});

export const send_screen_frame = spacetime.reducer({
  room_id: t.string(), seq: t.u32(), jpeg: t.byteArray(), width: t.u16(), height: t.u16()
}, (ctx, args) => {
  // The Python sender holds the room capability, but has its own HTTP identity.
  const row = liveIncident(ctx, args.room_id);
  if (!row) throw new SenderError('Call ended');
  if (!row.screen_sharing) throw new SenderError('Screen sharing paused');
  if (args.jpeg.length < 4 || args.jpeg.length > 200000 || args.jpeg[0] !== 0xff || args.jpeg[1] !== 0xd8 ||
      args.width < 1 || args.width > 1280 || args.height < 1) {
    throw new SenderError('Invalid screen frame');
  }
  if (row.status !== 'connected') return;
  const helper = ctx.db.presence.slot.find(`${args.room_id}:helper`);
  if (helper?.joined) ctx.db.screenFrameEvent.insert({ ...args, recipient: helper.identity, server_at: ctx.timestamp });
});

export const screen_visibility = spacetime.clientVisibilityFilter.sql(
  'SELECT * FROM screen_frame_event WHERE recipient = :sender'
);

// Capability-only preflight lets the HTTP sender wait without taking screenshots.
export const screen_share_ready = spacetime.reducer({ room_id: t.string() }, (ctx, { room_id }) => {
  const row = liveIncident(ctx, room_id);
  if (!row) throw new SenderError('Call ended');
  if (!row.screen_sharing) throw new SenderError('Screen sharing paused');
});
