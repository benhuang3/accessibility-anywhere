import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ConnectionId, Timestamp, createModuleTestHarness, TestAuth } from '../server/src/testSupport.ts';
import * as module from '../server/src/index.ts';
import { screenFreshness, swapMainView } from '../src/lib/screenPolicy.ts';
import AudioRow from '../src/module_bindings/audio_frame_event_table.ts';
import VideoRow from '../src/module_bindings/video_frame_event_table.ts';
import ScreenRow from '../src/module_bindings/screen_frame_event_table.ts';
import IncidentRow from '../src/module_bindings/call_incident_table.ts';

const room = '0123456789abcdef0123456789abcdef';

function fixture() {
  const h = createModuleTestHarness(module.default, module);
  const auth = (name: string) => TestAuth.fromJwtPayload(JSON.stringify({
    iss: 'https://ally-call.test', sub: name, aud: 'ally-call', exp: 4102444800
  }), new ConnectionId(1n));
  const elder = auth('elder'), helper = auth('helper'), sender = auth('python'), outsider = auth('other');
  h.withReducerTx(elder, ctx => {
    module.open_incident(ctx, { room_id: room, kind: 'other', helper_name: 'Priya' });
    module.join_call(ctx, { room_id: room, role: 'elder' });
  });
  h.withReducerTx(helper, ctx => {
    module.helper_responding(ctx, { room_id: room });
    module.join_call(ctx, { room_id: room, role: 'helper' });
  });
  const frame = { room_id: room, seq: 0, jpeg: new Uint8Array([255, 216, 255, 217]), width: 1280, height: 720 };
  return { h, elder, helper, sender, outsider, frame };
}

test('screen frames from HTTP identity reach only the same-room helper, and are transient', () => {
  const f = fixture();
  f.h.clock.set(new Timestamp(1234500n));
  assert.equal(f.h.db.incident.room_id.find(room)?.screen_sharing, true);
  f.h.withReducerTx(f.sender, ctx => {
    module.send_screen_frame(ctx, f.frame);
    const [event] = [...ctx.db.screenFrameEvent.iter()];
    assert.equal(event.room_id, room);
    assert.ok(event.recipient.isEqual(f.h.viewContext(f.helper).sender));
    assert.ok(!event.recipient.isEqual(f.h.viewContext(f.elder).sender));
    assert.ok(!event.recipient.isEqual(f.h.viewContext(f.outsider).sender));
    assert.equal(event.server_at.microsSinceUnixEpoch, 1234500n);
  });
  assert.equal([...f.h.db.screenFrameEvent.iter()].length, 0);
});

test('only elder can stop/resume; paused, ended, expired and invalid rooms reject screens', () => {
  const f = fixture();
  for (const auth of [f.helper, f.sender, f.outsider]) {
    assert.throws(() => f.h.withReducerTx(auth, ctx => module.set_screen_sharing(ctx, { room_id: room, on: false })));
  }
  f.h.withReducerTx(f.elder, ctx => module.set_screen_sharing(ctx, { room_id: room, on: false }));
  assert.throws(() => f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, f.frame)), /paused/);
  assert.throws(() => f.h.withReducerTx(f.sender, ctx => module.screen_share_ready(ctx, { room_id: room })), /paused/);
  f.h.withReducerTx(f.elder, ctx => module.set_screen_sharing(ctx, { room_id: room, on: true }));
  f.h.withReducerTx(f.sender, ctx => module.screen_share_ready(ctx, { room_id: room }));
  f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, f.frame));
  for (const room_id of ['short', 'a'.repeat(65), 'f'.repeat(32)]) {
    assert.throws(() => f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, { ...f.frame, room_id })));
  }
  f.h.clock.set(new Timestamp(2n * 60n * 60n * 1000000n));
  assert.throws(() => f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, f.frame)), /ended/);
  f.h.withReducerTx(f.helper, ctx => module.end_call(ctx, { room_id: room }));
  assert.throws(() => f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, f.frame)), /ended/);
});

test('screen dimensions, JPEG signature and 200KB cap are enforced', () => {
  const f = fixture();
  const max = new Uint8Array(200000); max.set([255, 216]);
  f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, { ...f.frame, jpeg: max }));
  for (const bad of [{ width: 1281 }, { width: 0 }, { height: 0 },
    { jpeg: new Uint8Array(200001) }, { jpeg: new Uint8Array(4) }, { jpeg: new Uint8Array(0) }]) {
    assert.throws(() => f.h.withReducerTx(f.sender, ctx => module.send_screen_frame(ctx, { ...f.frame, ...bad })));
  }
});

test('40ms and legacy 50ms audio are timestamped; other packet lengths reject', () => {
  const f = fixture();
  f.h.clock.set(new Timestamp(9000000n));
  for (const bytes of [1280, 1600]) {
    f.h.withReducerTx(f.elder, ctx => {
      module.send_audio_frame(ctx, { room_id: room, seq: 1, channels: 1, sample_rate: 16000, rms: 0, pcm16_le: new Uint8Array(bytes) });
      assert.equal([...ctx.db.audioFrameEvent.iter()][0].server_at.microsSinceUnixEpoch, 9000000n);
      module.send_video_frame(ctx, { room_id: room, seq: 1, width: 320, height: 180, jpeg: f.frame.jpeg });
      assert.equal([...ctx.db.videoFrameEvent.iter()][0].server_at.microsSinceUnixEpoch, 9000000n);
    });
  }
  for (const bytes of [0, 640, 1279, 1281, 1601]) {
    assert.throws(() => f.h.withReducerTx(f.elder, ctx => module.send_audio_frame(ctx, {
      room_id: room, seq: 1, channels: 1, sample_rate: 16000, rms: 0, pcm16_le: new Uint8Array(bytes)
    })));
  }
});

test('freshness exposes stale frames; swapping the face and screen is reversible', () => {
  assert.equal(screenFreshness(null, 5000), 'Waiting for the screen');
  assert.equal(screenFreshness(4000, 5000), 'Screen updated 1 s ago');
  assert.equal(screenFreshness(1000, 65000), 'Screen updated 64 s ago');
  assert.equal(screenFreshness(6000, 5000), 'Screen updated 0 s ago');
  assert.equal(swapMainView('screen'), 'face');
  assert.equal(swapMainView(swapMainView('screen')), 'screen');
});

test('local binding field types and binary order match the changed server rows', () => {
  const tables = module.default.schemaType.tables;
  for (const [binding, server] of [[AudioRow, tables.audioFrameEvent.rowType],
    [VideoRow, tables.videoFrameEvent.rowType], [ScreenRow, tables.screenFrameEvent.rowType],
    [IncidentRow, tables.incident.rowType]] as const) {
    const client = binding.algebraicType;
    if (client.tag !== 'Product') throw new Error('Expected row schema');
    assert.deepEqual(client.value.elements.map(column => column.algebraicType),
      server.elements.map(column => column.algebraicType));
  }
});
