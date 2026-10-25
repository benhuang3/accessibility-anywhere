// Schema mirror; replace with spacetime generate.
import { t } from 'spacetimedb';
export default { roomId: t.string(), seq: t.u32(), jpeg: t.byteArray(), width: t.u16(), height: t.u16() };
