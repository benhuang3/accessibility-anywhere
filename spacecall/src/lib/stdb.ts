import { get, writable } from 'svelte/store';
import { DbConnection, tables } from '../module_bindings';
import { STDB_URI, DB_NAME } from './config';
import { handleAudioEvent, handleVideoEvent, setTransmitting, stopCallRuntime, trackReducerCall, recordSendFailure } from './callRuntime';
import { handleScreenEvent, clearScreen } from './screenshare';
import { helperActions, incomingHelperAction, isHelperAction } from './helperActions';
import { INCIDENT_TTL_MS, type RoomLink } from './room';
import type { Incident } from './types';

export const incidentStore = writable<Incident | null>(null);
export const isConnected = writable(false);
export const connectionError = writable('');
let connection: DbConnection | null = null;
let current: RoomLink | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
let disposed = false;

function applyIncident(row: Incident) {
  if (row.roomId !== current?.roomId) return;
  const expired = Date.now() - Number(row.createdAt.microsSinceUnixEpoch / 1000n) >= INCIDENT_TTL_MS;
  incidentStore.set(expired ? { ...row, status: 'ended' } : row);
  setTransmitting(row.status === 'connected' && !expired);
  if (!row.screenSharing || row.status === 'ended' || expired) clearScreen();
  if (row.status === 'ended' || expired) stopCallRuntime();
}

export function connectStdb(link: RoomLink): Promise<DbConnection> {
  disposed = false;
  current = link;
  connectionError.set('');
  incidentStore.set(null);
  clearScreen(true);
  helperActions.reset();
  incomingHelperAction.set(null);
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      connectionError.set('The call could not connect. Please check your internet and open the link again.');
      disconnectStdb();
      reject(new Error('Connection timed out'));
    }, 20000);
    try {
      connection = DbConnection.builder().withUri(STDB_URI).withDatabaseName(DB_NAME)
        .onConnect((conn, identity) => {
          if (disposed) { conn.disconnect(); return; }
          conn.db.callIncident.onInsert((_ctx, row) => applyIncident(row));
          conn.db.callIncident.onUpdate((_ctx, _old, row) => applyIncident(row));
          conn.db.audioFrameEvent.onInsert((_ctx, row) => handleAudioEvent(row));
          conn.db.videoFrameEvent.onInsert((_ctx, row) => handleVideoEvent(row));
          conn.db.screenFrameEvent.onInsert((_ctx, row) => {
            const incident = get(incidentStore);
            if (link.role === 'helper' && row.roomId === link.roomId && incident?.screenSharing &&
                incident.status === 'connected') handleScreenEvent(row);
          });
          conn.db.helperActionEvent.onInsert((_ctx, row) => {
            if (link.role === 'elder' && row.roomId === link.roomId && isHelperAction(row)) incomingHelperAction.set(row);
          });
          conn.db.actionResultEvent.onInsert((_ctx, row) => {
            if (link.role === 'helper' && row.roomId === link.roomId) helperActions.complete(row.seq, row.ok);
          });
          conn.subscriptionBuilder().onApplied(() => {
            clearTimeout(timeout);
            isConnected.set(true);
            resolve(conn);
          }).onError(() => {
            clearTimeout(timeout);
            connectionError.set('This call could not connect. Please open the link again.');
            reject(new Error('Call subscription failed'));
            disconnectStdb();
          }).subscribe([
            tables.callIncident.where(row => row.roomId.eq(link.roomId)),
            tables.audioFrameEvent.where(row => row.roomId.eq(link.roomId).and(row.recipient.eq(identity))),
            tables.videoFrameEvent.where(row => row.roomId.eq(link.roomId).and(row.recipient.eq(identity))),
            tables.screenFrameEvent.where(row => row.roomId.eq(link.roomId).and(row.recipient.eq(identity))),
            tables.helperActionEvent.where(row => row.roomId.eq(link.roomId).and(row.recipient.eq(identity))),
            tables.actionResultEvent.where(row => row.roomId.eq(link.roomId).and(row.recipient.eq(identity)))
          ]);
          heartbeatTimer = setInterval(() => {
            const row = get(incidentStore);
            if (!row || row.status === 'ended') return;
            if (Date.now() - Number(row.createdAt.microsSinceUnixEpoch / 1000n) >= INCIDENT_TTL_MS) {
              applyIncident({ ...row, status: 'ended' });
            }
            void trackReducerCall(() => conn.reducers.heartbeat({ roomId: link.roomId })).catch(recordSendFailure);
          }, 15000);
        }).onConnectError(() => {
          clearTimeout(timeout);
          connectionError.set('The call could not connect. Please check your internet and open the link again.');
          reject(new Error('Connection failed'));
        }).onDisconnect(() => {
          clearTimeout(timeout);
          isConnected.set(false);
          clearInterval(heartbeatTimer);
          setTransmitting(false);
          stopCallRuntime();
          clearScreen(true);
          helperActions.reset();
          incomingHelperAction.set(null);
          if (!disposed && get(incidentStore)?.status !== 'ended') {
            connectionError.set('The connection was lost. Please open a new call from Ally.');
          }
          reject(new Error('Connection closed'));
        }).build();
    } catch (error) {
      clearTimeout(timeout);
      reject(error);
    }
  });
}

export function disconnectStdb() {
  disposed = true;
  clearInterval(heartbeatTimer);
  connection?.disconnect();
  connection = null;
  helperActions.reset();
  incomingHelperAction.set(null);
  isConnected.set(false);
  stopCallRuntime();
  clearScreen(true);
}
