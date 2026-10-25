// Hand-written SDK bindings: replace with spacetime generate before publishing.
// Wire field order and names mirror server/src/index.ts (SpacetimeDB 2.13).
import {
  DbConnectionBuilder as ConnectionBuilder, DbConnectionImpl,
  SubscriptionBuilderImpl, schema, table, t, reducers as defineReducers,
  reducerSchema, procedures, makeQueryBuilder, convertToAccessorMap,
  type DbConnectionConfig, type RemoteModule
} from 'spacetimedb';

const tablesSchema = schema({
  callIncident: table({ name: 'call_incident' }, {
    roomId: t.string().name('room_id').primaryKey(), kind: t.string(), status: t.string(),
    createdAt: t.timestamp().name('created_at'), updatedAt: t.timestamp().name('updated_at'),
    helperName: t.string().name('helper_name')
  }),
  audioFrameEvent: table({ name: 'audio_frame_event', event: true }, {
    roomId: t.string().name('room_id'), sender: t.identity(), recipient: t.identity(),
    seq: t.u32(), sampleRate: t.u32().name('sample_rate'), channels: t.u8(), rms: t.f32(),
    pcm16Le: t.byteArray().name('pcm16_le')
  }),
  videoFrameEvent: table({ name: 'video_frame_event', event: true }, {
    roomId: t.string().name('room_id'), sender: t.identity(), recipient: t.identity(),
    seq: t.u32(), width: t.u16(), height: t.u16(), jpeg: t.byteArray()
  })
});
const reducersSchema = defineReducers(
  reducerSchema('open_incident', { roomId: t.string(), kind: t.string(), helperName: t.string() }),
  reducerSchema('helper_responding', { roomId: t.string() }),
  reducerSchema('join_call', { roomId: t.string(), role: t.string() }),
  reducerSchema('end_call', { roomId: t.string() }),
  reducerSchema('heartbeat', { roomId: t.string() }),
  reducerSchema('send_audio_frame', {
    roomId: t.string(), seq: t.u32(), sampleRate: t.u32(), channels: t.u8(), rms: t.f32(), pcm16Le: t.byteArray()
  }),
  reducerSchema('send_video_frame', {
    roomId: t.string(), seq: t.u32(), width: t.u16(), height: t.u16(), jpeg: t.byteArray()
  })
);
const procedureSchema = procedures();
const REMOTE_MODULE = {
  versionInfo: { cliVersion: '2.13.0' as const },
  tables: tablesSchema.schemaType.tables,
  reducers: reducersSchema.reducersType.reducers,
  ...procedureSchema
} satisfies RemoteModule<typeof tablesSchema.schemaType, typeof reducersSchema.reducersType, typeof procedureSchema>;
export const tables = makeQueryBuilder(tablesSchema.schemaType);
export const reducers = convertToAccessorMap(reducersSchema.reducersType.reducers);
export class SubscriptionBuilder extends SubscriptionBuilderImpl<typeof REMOTE_MODULE> {}
export class DbConnectionBuilder extends ConnectionBuilder<DbConnection> {}
export class DbConnection extends DbConnectionImpl<typeof REMOTE_MODULE> {
  static builder = (): DbConnectionBuilder => new DbConnectionBuilder(
    REMOTE_MODULE, (config: DbConnectionConfig<typeof REMOTE_MODULE>) => new DbConnection(config)
  );
  override subscriptionBuilder = (): SubscriptionBuilder => new SubscriptionBuilder(this);
}
