// Tests outside server/ must resolve the same SDK instance as the module.
// This file is never imported by the published index.ts entry point.
export { createModuleTestHarness, TestAuth } from 'spacetimedb/server/test-utils';
export { ConnectionId, Timestamp } from 'spacetimedb';
