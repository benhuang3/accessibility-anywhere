import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ConnectionId, Timestamp } from 'spacetimedb';
import { createModuleTestHarness, TestAuth } from 'spacetimedb/server/test-utils';
import * as module from '../src/index.ts';
import { parseRoomHash } from '../../src/lib/room.ts';

const room = '0123456789abcdef0123456789abcdef';
const otherRoom = 'fedcba9876543210fedcba9876543210';

function fixture() {
  const harness = createModuleTestHarness(module.default, module);
  const auth = (name: string, connection = 1n) => TestAuth.fromJwtPayload(JSON.stringify({
    iss: 'https://ally-call.test', sub: name, aud: 'ally-call', exp: 4102444800
  }), new ConnectionId(connection));
  const elder = auth('elder');
  const helper = auth('helper', 2n);
  const outsider = auth('outsider', 3n);
  const open = (id = room) => harness.withReducerTx(elder, ctx => module.open_incident(ctx, {
    room_id: id, kind: 'remote_access', helper_name: 'Priya'
  }));
  const join = () => {
    harness.withReducerTx(elder, ctx => module.join_call(ctx, { room_id: room, role: 'elder' }));
    harness.withReducerTx(helper, ctx => module.helper_responding(ctx, { room_id: room }));
    harness.withReducerTx(helper, ctx => module.join_call(ctx, { room_id: room, role: 'helper' }));
  };
  return { harness, elder, helper, outsider, open, join, auth };
}

test('room links accept URL-safe IDs, both roles and encoded names, and reject malformed data', () => {
  assert.deepEqual(parseRoomHash(`#room=${room}&role=helper&name=Priya%20Rao&kind=remote_access`), {
    roomId: room, role: 'helper', name: 'Priya Rao', kind: 'remote_access'
  });
  for (const hash of ['', '#room=short&role=elder', `#room=${room}&role=visitor`,
    `#room=${'a'.repeat(65)}&role=elder`, `#room=${room}&role=elder&kind=https://bank.test`,
    `#room=${room}&role=elder&kind=constructor`, `#room=${room}&role=elder&name=%00`]) {
    assert.throws(() => parseRoomHash(hash));
  }
});

test('two roles transition ringing to responding to connected, and cannot switch roles or hijack', () => {
  const f = fixture();
  f.open();
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'ringing');
  f.harness.withReducerTx(f.helper, ctx => module.helper_responding(ctx, { room_id: room }));
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'responding');
  f.join();
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'connected');
  assert.throws(() => f.harness.withReducerTx(f.outsider, ctx => module.helper_responding(ctx, { room_id: room })));
  assert.throws(() => f.harness.withReducerTx(f.helper, ctx => module.join_call(ctx, { room_id: room, role: 'elder' })));
  assert.throws(() => f.harness.withReducerTx(f.outsider, ctx => module.end_call(ctx, { room_id: room })));
});

test('private incident view reveals only rooms the sender has claimed', () => {
  const f = fixture();
  f.open(); f.open(otherRoom);
  f.harness.withReducerTx(f.helper, ctx => module.helper_responding(ctx, { room_id: room }));
  assert.equal(module.call_incident(f.harness.viewContext(f.outsider)).length, 0);
  assert.deepEqual(module.call_incident(f.harness.viewContext(f.helper)).map(row => row.room_id), [room]);
  assert.equal(module.call_incident(f.harness.viewContext(f.elder)).length, 2);
});

test('module rejects invalid room IDs and kind payloads transactionally', () => {
  const f = fixture();
  for (const room_id of ['short', 'a'.repeat(65), 'a'.repeat(16) + "' OR 1=1", 'a'.repeat(16) + '/']) {
    assert.throws(() => f.open(room_id));
  }
  assert.throws(() => f.harness.withReducerTx(f.elder, ctx => module.open_incident(ctx, {
    room_id: room, kind: 'https://private-bank.test/account', helper_name: 'Priya'
  })));
  assert.equal([...f.harness.db.incident.iter()].length, 0);
});

test('media derives recipient from same room and rejects outsiders or malformed frames', () => {
  const f = fixture(); f.open(); f.join();
  const frame = { room_id: room, seq: 0, sample_rate: 16000, channels: 1, rms: 0, pcm16_le: new Uint8Array(1600) };
  f.harness.withReducerTx(f.elder, ctx => {
    module.send_audio_frame(ctx, frame);
    const event = [...ctx.db.audioFrameEvent.iter()][0];
    assert.equal(event.room_id, room);
    assert.ok(event.recipient.isEqual(f.harness.viewContext(f.helper).sender));
  });
  assert.equal([...f.harness.db.audioFrameEvent.iter()].length, 0, 'event rows leave the active database after commit');
  assert.throws(() => f.harness.withReducerTx(f.outsider, ctx => module.send_audio_frame(ctx, frame)));
  assert.throws(() => f.harness.withReducerTx(f.elder, ctx => module.send_audio_frame(ctx, { ...frame, sample_rate: 8000 })));
  assert.throws(() => f.harness.withReducerTx(f.elder, ctx => module.send_audio_frame(ctx, { ...frame, pcm16_le: new Uint8Array(64000) })));
  assert.throws(() => f.harness.withReducerTx(f.elder, ctx => module.send_video_frame(ctx, {
    room_id: room, seq: 0, width: 1920, height: 1080, jpeg: new Uint8Array([255, 216, 255, 217])
  })));
});

test('two-hour expiry commits ended state, drops media and never reopens the room', () => {
  const f = fixture(); f.open(); f.join();
  f.harness.clock.set(new Timestamp(2n * 60n * 60n * 1000000n));
  f.harness.withReducerTx(f.elder, ctx => module.send_video_frame(ctx, {
    room_id: room, seq: 0, width: 320, height: 180, jpeg: new Uint8Array([255, 216, 255, 217])
  }));
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'ended');
  f.open();
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'ended');
});

test('simultaneous rooms cannot receive or send each other\'s media', () => {
  const f = fixture(); f.open(); f.join();
  const secondElder = f.auth('second-elder', 4n);
  const secondHelper = f.auth('second-helper', 5n);
  f.harness.withReducerTx(secondElder, ctx => {
    module.open_incident(ctx, { room_id: otherRoom, kind: 'scam_popup', helper_name: 'Mira' });
    module.join_call(ctx, { room_id: otherRoom, role: 'elder' });
  });
  f.harness.withReducerTx(secondHelper, ctx => {
    module.helper_responding(ctx, { room_id: otherRoom });
    module.join_call(ctx, { room_id: otherRoom, role: 'helper' });
  });
  const video = { room_id: otherRoom, seq: 0, width: 480, height: 270, jpeg: new Uint8Array([255, 216, 255, 217]) };
  assert.throws(() => f.harness.withReducerTx(f.helper, ctx => module.send_video_frame(ctx, video)));
  f.harness.withReducerTx(secondElder, ctx => {
    module.send_video_frame(ctx, video);
    const event = [...ctx.db.videoFrameEvent.iter()][0];
    assert.equal(event.room_id, otherRoom);
    assert.ok(event.recipient.isEqual(f.harness.viewContext(secondHelper).sender));
    assert.ok(!event.recipient.isEqual(f.harness.viewContext(f.helper).sender));
  });
});

test('End call is final and disables media, even if a helper joins again', () => {
  const f = fixture(); f.open(); f.join();
  f.harness.withReducerTx(f.helper, ctx => module.end_call(ctx, { room_id: room }));
  f.open();
  f.harness.withReducerTx(f.helper, ctx => module.helper_responding(ctx, { room_id: room }));
  f.harness.withReducerTx(f.elder, ctx => {
    module.send_video_frame(ctx, {
      room_id: room, seq: 0, width: 320, height: 180, jpeg: new Uint8Array([255, 216, 255, 217])
    });
    assert.equal([...ctx.db.videoFrameEvent.iter()].length, 0);
  });
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'ended');
});

test('an old link arriving after expiry can see that the call has ended', () => {
  const f = fixture(); f.open();
  f.harness.clock.set(new Timestamp(3n * 60n * 60n * 1000000n));
  f.harness.withReducerTx(f.helper, ctx => module.helper_responding(ctx, { room_id: room }));
  assert.equal(module.call_incident(f.harness.viewContext(f.helper))[0].status, 'ended');
});

test('disconnect ends call and stale disconnect cannot close a newer connection', () => {
  const f = fixture(); f.open(); f.join();
  const reconnected = f.auth('elder', 4n);
  f.harness.withReducerTx(reconnected, ctx => module.open_incident(ctx, {
    room_id: room, kind: 'remote_access', helper_name: 'Priya'
  }));
  f.harness.withReducerTx(f.elder, ctx => module.client_disconnected(ctx, {}));
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'connected');
  f.harness.withReducerTx(reconnected, ctx => module.client_disconnected(ctx, {}));
  assert.equal(f.harness.db.incident.room_id.find(room)?.status, 'ended');
});

test('face profiles are private to their owner, upserted and deletable', () => {
  const f = fixture();
  const profile = JSON.stringify({ version: 1, gaze: { coef: [1, 2] } });
  f.harness.withReducerTx(f.elder, ctx => module.save_face_profile(ctx, { profile_json: profile }));
  assert.equal(module.own_face_profile(f.harness.viewContext(f.outsider)).length, 0);
  assert.equal(module.own_face_profile(f.harness.viewContext(f.elder))[0].profile_json, profile);
  f.harness.withReducerTx(f.elder, ctx => module.save_face_profile(ctx, { profile_json: profile }));
  assert.equal([...f.harness.db.face_profile.iter()].length, 1);
  assert.throws(() => f.harness.withReducerTx(f.elder, ctx => module.save_face_profile(ctx, {
    profile_json: JSON.stringify({ version: 1, fill: 'x'.repeat(64 * 1024) })
  })));
  f.harness.withReducerTx(f.outsider, ctx => module.delete_face_profile(ctx, {}));
  assert.equal(f.harness.db.face_profile.owner.find(f.harness.viewContext(f.elder).sender)?.profile_json, profile);
  f.harness.withReducerTx(f.elder, ctx => module.delete_face_profile(ctx, {}));
  assert.equal(module.own_face_profile(f.harness.viewContext(f.elder)).length, 0);
});
