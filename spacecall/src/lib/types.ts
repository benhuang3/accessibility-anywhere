import type { DbConnection } from '../module_bindings';

type RowOf<T extends (...args: never[]) => Iterable<unknown>> = ReturnType<T> extends Iterable<infer Row> ? Row : never;
export type Incident = RowOf<DbConnection['db']['callIncident']['iter']>;
export type AudioFrame = RowOf<DbConnection['db']['audioFrameEvent']['iter']>;
export type VideoFrame = RowOf<DbConnection['db']['videoFrameEvent']['iter']>;
export type ScreenFrame = RowOf<DbConnection['db']['screenFrameEvent']['iter']>;
