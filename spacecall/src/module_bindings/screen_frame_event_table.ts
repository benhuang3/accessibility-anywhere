// Schema mirror for local checks; replace with spacetime generate before publishing the client.
import { t } from 'spacetimedb';
export default t.row({
  roomId: t.string().name('room_id'), recipient: t.identity(), seq: t.u32(),
  jpeg: t.byteArray(), width: t.u16(), height: t.u16(), serverAt: t.timestamp().name('server_at')
});
