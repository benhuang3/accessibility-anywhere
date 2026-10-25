<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import type { DbConnection } from '../module_bindings';
  import { connectStdb, disconnectStdb, incidentStore, isConnected, connectionError } from '$lib/stdb';
  import {
    startCallRuntime, stopCallRuntime, resumeCallAudio, setTransmitting,
    localVideoStream, remoteVideoUrl, muted, toggleMute, flipCamera,
    connectionSlow, audioNeedsTap, mediaError
  } from '$lib/callRuntime';
  import { parseRoomHash, kindLabels, type RoomLink } from '$lib/room';

  let link: RoomLink | null = null;
  let conn: DbConnection | null = null;
  let localEl: HTMLVideoElement;
  let joined = false;
  let busy = false;
  let ended = false;
  let permissionPending = false;
  let message = '';
  let cameraBusy = false;

  $: helperName = $incidentStore?.helperName ?? link?.name ?? 'your helper';
  $: isHelper = link?.role === 'helper';
  $: callEnded = ended || $incidentStore?.status === 'ended';
  $: status = callEnded ? 'Call ended'
    : $incidentStore?.status === 'connected' ? (isHelper ? 'You are together' : `${helperName} is here`)
    : $incidentStore?.status === 'responding' ? (isHelper ? 'Joining the call\u2026' : `${helperName} is on her way`)
    : isHelper ? 'Someone you love needs you' : `Calling ${helperName}\u2026`;
  $: if (localEl) localEl.srcObject = $localVideoStream;

  async function prepareMedia(connection: DbConnection, room: RoomLink) {
    permissionPending = true;
    try {
      await startCallRuntime(connection, room.roomId);
      if (ended) return;
      setTransmitting(get(incidentStore)?.status === 'connected');
    } finally {
      permissionPending = false;
    }
  }

  async function openElder(connection: DbConnection, room: RoomLink) {
    await connection.reducers.openIncident({ roomId: room.roomId, kind: room.kind, helperName: room.name });
    if (ended || get(incidentStore)?.status === 'ended') return;
    await prepareMedia(connection, room);
    if (ended) return;
    await connection.reducers.joinCall({ roomId: room.roomId, role: 'elder' });
    joined = true;
  }

  async function joinHelper() {
    if (!conn || !link || busy || callEnded) return;
    busy = true;
    message = '';
    // These start within the tap, before awaiting a reducer or browser permission.
    void resumeCallAudio();
    const media = prepareMedia(conn, link);
    try {
      await Promise.all([conn.reducers.helperResponding({ roomId: link.roomId }), media]);
      if (ended || get(incidentStore)?.status === 'ended') { stopCallRuntime(); return; }
      await conn.reducers.joinCall({ roomId: link.roomId, role: 'helper' });
      joined = true;
    } catch {
      stopCallRuntime();
      message = get(mediaError) || 'This call is unavailable. Please ask Ally to start a new call.';
    } finally {
      busy = false;
    }
  }

  async function endCall() {
    ended = true;
    stopCallRuntime();
    const disconnectTimer = setTimeout(disconnectStdb, 2000);
    try {
      if (conn && link) await conn.reducers.endCall({ roomId: link.roomId });
    } catch {
      // Disconnect also ends the room through the server lifecycle reducer.
    } finally {
      clearTimeout(disconnectTimer);
      disconnectStdb();
    }
  }

  async function switchCamera() {
    if (cameraBusy) return;
    cameraBusy = true;
    try { await flipCamera(); } finally { cameraBusy = false; }
  }

  onMount(() => {
    try {
      link = parseRoomHash(window.location.hash);
      const room = link;
      void connectStdb(room).then(async connection => {
        if (ended) { disconnectStdb(); return; }
        conn = connection;
        if (room.role === 'elder') await openElder(connection, room);
      }).catch(() => {
        if (!ended) message = get(mediaError) || get(connectionError) || 'This call is unavailable. Please ask Ally to start a new call.';
      });
    } catch (error) {
      message = error instanceof Error ? error.message : 'Please open the link Ally sent you.';
    }
    const leave = () => { ended = true; disconnectStdb(); };
    window.addEventListener('pagehide', leave);
    return () => { window.removeEventListener('pagehide', leave); leave(); };
  });
</script>

<svelte:head>
  <title>Ally Call &#8212; A familiar face, right here</title>
  <meta name="description" content="A private call to someone you trust, with Ally." />
  <meta name="referrer" content="no-referrer" />
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>

<main class:helper={isHelper} class:ended={callEnded}>
  <div class="backdrop" aria-hidden="true"></div>
  {#if $remoteVideoUrl && !callEnded}
    <img class="remote-video" src={$remoteVideoUrl} alt={isHelper ? 'Live video of your family member' : `Live video of ${helperName}`} />
  {/if}

  <header class="glass topbar">
    <div class="brand"><span class="brand-mark" aria-hidden="true">a</span> Ally Call</div>
    <p class="status" role="status" aria-live="polite">{link ? status : 'A familiar face, right here'}</p>
  </header>

  {#if !link}
    <section class="center-card glass">
      <p class="eyebrow">Someone you trust. Just a call away.</p>
      <h1>Ally Call</h1>
      <p role="alert">{message || 'Please open the call link Ally sent you.'}</p>
    </section>
  {:else if callEnded}
    <section class="center-card glass" aria-live="polite">
      <span class="portrait" aria-hidden="true">&#10003;</span>
      <h1>Call ended</h1>
      <p>You can close this page now.</p>
    </section>
  {:else if isHelper && !joined}
    <section class="center-card glass invitation">
      <p class="eyebrow"><span class="caution-dot" aria-hidden="true"></span> A moment for family</p>
      <h1>Be their familiar face.</h1>
      <p>Ally stopped a possible scam on {kindLabels[link.kind]}. They're waiting for you.</p>
      {#if permissionPending}
        <p class="notice" role="status">Allow camera and microphone so you can see and hear each other.</p>
      {/if}
      {#if message || $connectionError || $mediaError}
        <p class="notice" role="alert">{message || $connectionError || $mediaError}</p>
      {/if}
      <button class="join" onclick={joinHelper} disabled={!$isConnected || busy}>
        {busy ? 'Joining call\u2026' : 'Join call'}
      </button>
      {#if !$isConnected && !$connectionError}<p class="connection-note">Connecting your call&#8230;</p>{/if}
    </section>
  {:else}
    {#if !$remoteVideoUrl}
      <section class="waiting" aria-label="Waiting for video">
        <span class="portrait" aria-hidden="true">{isHelper ? 'a' : helperName.charAt(0).toUpperCase()}</span>
        <h1>{isHelper ? 'You are close by.' : 'A familiar face is on the way.'}</h1>
        <p>{isHelper ? 'Your family member will appear here.' : `${helperName} will appear here.`}</p>
      </section>
    {/if}
    {#if $localVideoStream}
      <aside class="self-view glass" aria-label="Your camera">
        <video autoplay playsinline muted bind:this={localEl}></video>
        <span>You</span>
      </aside>
    {/if}
    <div class="notices" aria-live="polite">
      {#if permissionPending}
        <p class="glass notice">Allow camera and microphone so you can see and hear {isHelper ? 'each other' : helperName}.</p>
      {/if}
      {#if $audioNeedsTap}
        <button class="glass audio-unlock" onclick={() => void resumeCallAudio()}>Tap to hear {isHelper ? 'your family member' : helperName}</button>
      {/if}
      {#if message || $connectionError || $mediaError}
        <p class="glass notice" role="alert">{message || $connectionError || $mediaError}</p>
      {/if}
      {#if $connectionSlow}<p class="glass slow">Connection is slow</p>{/if}
    </div>
    <nav class="glass controls" aria-label="Call controls">
      <button class="secondary" onclick={toggleMute} aria-pressed={$muted} disabled={!$localVideoStream}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1M12 18v4M8 22h8"/>{#if $muted}<path d="m3 3 18 18"/>{/if}</svg>
        {$muted ? 'Unmute' : 'Mute'}
      </button>
      {#if isHelper}
        <button class="secondary flip" onclick={switchCamera} disabled={cameraBusy || !$localVideoStream}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M4 7h4l2-3h4l2 3h4v13H4zM9 11l-2 2 2 2m6-4 2 2-2 2M7 13h10"/></svg>
          Flip camera
        </button>
      {/if}
      <button class="end-call" onclick={endCall}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 16v-4c5-5 13-5 18 0v4h-5v-4M8 12v4H3"/></svg>
        End call
      </button>
    </nav>
  {/if}

  <footer><a href="https://github.com/Lethalchip/SpaceChatDB" target="_blank" rel="noreferrer noopener">Built on SpaceChatDB</a></footer>
</main>
