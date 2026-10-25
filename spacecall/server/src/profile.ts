import { table, t } from 'spacetimedb/server';

// The entry point supplies this table to its shared schema and declares reducers there.
export const profileTable = () => table({ name: 'face_profile' }, {
  owner: t.identity().primaryKey(),
  profile_json: t.string(),
  version: t.u32(),
  updated_at: t.timestamp()
});
