<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import type { DbConnection } from '../module_bindings';
  import { connectStdb, disconnectStdb, incidentStore, isConnected, connectionError } from '$lib/stdb';
  import {
    startCallRuntime, stopCallRuntime, resumeCallAudio, setTransmitting,
    localVideoStream, remoteVideoUrl, muted, toggleMute, flipCamera,
    connectionSlow, audioNeedsTap, mediaError, callStats
  } from '$lib/callRuntime';
  import { helperActions, incomingHelperAction, createElderRelay, actionDisabledReason, type HelperActionKind, type Point } from '$lib/helperActions';
  import { remoteScreen } from '$lib/screenshare';
  import SharedScreen from '$lib/SharedScreen.svelte';
  import { screenFreshness } from '$lib/screenPolicy';
  import { parseRoomHash, kindLabels, type RoomLink } from '$lib/room';

  const { pending: pendingActions, results: actionResults } = helperActions;

  let link: RoomLink | null = null;
  let conn: DbConnection | null = null;
  let localEl: HTMLVideoElement;
  let joined = false;
  let busy = false;
  let ended = false;
  let permissionPending = false;
  let message = '';
  let cameraBusy = false;
  let debug = false;
  let sharingBusy = false;
  let sharingAvailable = true;
  let faceExpanded = false;
  let selfHidden = false;
  let elderName = 'Family';
  let now = Date.now();

  $: helperName = $incidentStore?.helperName ?? link?.name ?? 'your helper';
  $: screenHint = screenFreshness($remoteScreen?.updatedAt ?? null, now);
  $: sharingOn = sharingAvailable && ($incidentStore?.screenSharing ?? true);
  $: actionReason = actionDisabledReason($isConnected && joined && !callEnded && $incidentStore?.status === 'connected', sharingOn);
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
    if (new URLSearchParams(window.location.hash.slice(1)).get('screen') === 'off') {
      await connection.reducers.setScreenSharing({ roomId: room.roomId, on: false });
    }
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

  async function toggleScreenSharing() {
    if (!conn || !link || sharingBusy) return;
    sharingBusy = true;
    message = '';
    try {
      await conn.reducers.setScreenSharing({ roomId: link.roomId, on: !sharingOn });
    } catch {
      message = 'Screen sharing could not change. Please try again or end the call.';
    } finally {
      sharingBusy = false;
    }
  }

  function sendHelperAction(action: HelperActionKind, point?: Point): boolean {
    if (actionReason || !conn || !link) return false;
    const connection = conn, roomId = link.roomId;
    return helperActions.send(action, row => connection.reducers.sendHelperAction({ roomId, ...row }), point) !== null;
  }

  onMount(() => {
    const relay = createElderRelay(window, async (seq, ok) => {
      if (conn && link?.role === 'elder') await conn.reducers.reportActionResult({ roomId: link.roomId, seq, ok });
    });
    const stopActions = incomingHelperAction.subscribe(row => {
      if (row && link?.role === 'elder' && conn) relay.handle(row);
    });
    const params = new URLSearchParams(window.location.hash.slice(1));
    sharingAvailable = params.get('screen') !== 'off';
    elderName = params.get('elderName')?.trim().slice(0, 64) || 'Family';
    const freshnessTimer = setInterval(() => { now = Date.now(); }, 1000);
    const updateDebug = () => { debug = new URLSearchParams(window.location.hash.slice(1)).get('debug') === '1'; };
    updateDebug();
    window.addEventListener('hashchange', updateDebug);
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
    return () => {
      stopActions();
      relay.stop();
      clearInterval(freshnessTimer);
      window.removeEventListener('pagehide', leave);
      window.removeEventListener('hashchange', updateDebug);
      leave();
    };
  });
</script>

<svelte:head>
  <title>Ally Call &#8212; A familiar face, right here</title>
  <meta name="description" content="A private call to someone you trust, with Ally." />
  <meta name="referrer" content="no-referrer" />
  <meta name="robots" content="noindex, nofollow" />
</svelte:head>


{#snippet selfView()}
  {#if $localVideoStream && !selfHidden}
      <aside class="self-view glass" aria-label="Your camera">
        <video autoplay playsinline muted bind:this={localEl}></video>
        <span>You</span>
        {#if isHelper}<button class="hide-self" onclick={() => { selfHidden = true; }} aria-label="Hide your camera preview">Hide</button>{/if}
      </aside>
    {/if}
  {#if isHelper && selfHidden && $localVideoStream}<button class="secondary show-self" onclick={() => { selfHidden = false; }}>Show my camera</button>{/if}
{/snippet}

{#snippet callNotices()}
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
{/snippet}

{#snippet callControls()}
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
{/snippet}

<main class:helper={isHelper} class:helper-call={isHelper && joined && !callEnded} class:elder-call={link && !isHelper && !callEnded} class:ended={callEnded}>
  <div class="backdrop" aria-hidden="true"></div>
  {#if $remoteVideoUrl && !callEnded && !isHelper}
    <img class="remote-video" src={$remoteVideoUrl} alt={isHelper ? 'Live video of your family member' : `Live video of ${helperName}`} />
  {/if}

  <header class="glass topbar">
    <div class="brand"><span class="brand-mark" aria-hidden="true">a</span> Ally Call</div>
    <p class="status" role="status" aria-live="polite">{link ? status : 'A familiar face, right here'}</p>
  </header>

  {#if link && !isHelper && !callEnded}
    <section class="glass sharing-banner" aria-label="Screen sharing">
      <p role="status">{sharingOn ? `${helperName} can see your screen` : 'Screen sharing is off'}</p>
      <button class="secondary" onclick={toggleScreenSharing} disabled={!$incidentStore || sharingBusy || !sharingAvailable}>
        {!sharingAvailable ? 'Start a new call from Ally to share' : sharingOn ? 'Stop sharing' : 'Share my screen again'}
      </button>
    </section>
  {/if}

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
    {#if isHelper}
      <section class="helper-workspace" aria-label="Your family member's screen and camera">
        <SharedScreen screen={$remoteScreen} sharing={sharingOn} hint={screenHint}
          disabledReason={actionReason} pending={$pendingActions} results={$actionResults} onaction={sendHelperAction} />
        <aside class="helper-rail" aria-label="Family and call controls">
          <div class="helper-faces">
            <button class="glass family-tile" onclick={() => { faceExpanded = !faceExpanded; }}
              aria-label={`Enlarge ${elderName}'s camera`} aria-expanded={faceExpanded} disabled={!$remoteVideoUrl}>
              {#if $remoteVideoUrl}<img src={$remoteVideoUrl} alt={`Live video of ${elderName}`} />
              {:else}<span class="face-placeholder" aria-hidden="true">{elderName.charAt(0).toUpperCase()}</span>{/if}
              <span class="tile-label">{elderName}</span>
            </button>
            {@render selfView()}
          </div>
          <div class="rail-actions">
            {@render callNotices()}
            {@render callControls()}
          </div>
        </aside>
      </section>
      {#if faceExpanded && $remoteVideoUrl}
        <div class="face-expanded" role="presentation">
          <button class="glass face-close" onclick={() => { faceExpanded = false; }} aria-label="Return to shared screen">
            <img src={$remoteVideoUrl} alt={`Live video of ${elderName}`} />
            <span>Back to screen</span>
          </button>
        </div>
      {/if}
    {:else}
      {@render selfView()}
      {@render callNotices()}
      {@render callControls()}
    {/if}
    {#if !$remoteVideoUrl && !isHelper}
      <section class="waiting" aria-label="Waiting for video">
        <span class="portrait" aria-hidden="true">{isHelper ? 'a' : helperName.charAt(0).toUpperCase()}</span>
        <h1>{isHelper ? 'You are close by.' : 'A familiar face is on the way.'}</h1>
        <p>{isHelper ? 'Your family member will appear here.' : `${helperName} will appear here.`}</p>
      </section>
    {/if}

  {/if}

  {#if debug}
    <aside class="call-debug" aria-label="Call debug metrics">
      <p>Video fps ↑ {$callStats.videoUp.toFixed(1)} ↓ {$callStats.videoDown.toFixed(1)}</p>
      <p>Audio chunks/s ↑ {$callStats.audioUp.toFixed(1)} ↓ {$callStats.audioDown.toFixed(1)}</p>
      <p>Media kbps ↑ {$callStats.kbpsUp.toFixed(1)} ↓ {$callStats.kbpsDown.toFixed(1)}</p>
      <p>Screen {$callStats.screenDown.toFixed(1)} fps · {$callStats.screenKbps.toFixed(1)} kbps</p>
      <p>Camera target {$callStats.cameraTarget} fps · Observed call {($callStats.callBytesPerSecond / 1000).toFixed(1)} KB/s (10 s)</p>
      <p>Playback buffer {$callStats.playbackMs.toFixed(0)} ms · Pending reducers {$callStats.pending}</p>
      <p>One-way estimate: {$callStats.delayMs === null ? 'unavailable (no server timestamp)' : `${$callStats.delayMs.toFixed(0)} ms (server → here; clocks may differ)`}</p>
    </aside>
  {/if}
  <footer><a href="https://github.com/Lethalchip/SpaceChatDB" target="_blank" rel="noreferrer noopener">Built on SpaceChatDB</a></footer>
</main>
