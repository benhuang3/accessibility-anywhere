<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { actionKinds, actionLabel, normalizedPoint, type HelperAction, type HelperActionKind, type ActionResults, type Point } from './helperActions';
  import type { SharedScreen } from './screenshare';
  import { fitScreen, toggleScreenZoom, type ScreenZoom } from './screenPolicy';

  export let screen: SharedScreen | null = null;
  export let sharing = true;
  export let hint = '';
  export let disabledReason = '';
  export let pending: Record<number, HelperAction> = {};
  export let results: ActionResults = {};
  export let onaction: (action: HelperActionKind, point?: Point) => boolean = () => false;

  let pointMode = false;
  let marker: Point | null = null;
  let markerTimer: ReturnType<typeof setTimeout>;
  $: pendingKinds = Object.values(pending).map(row => row.action);
  $: if (disabledReason || !screen || !sharing) { pointMode = false; marker = null; }
  onDestroy(() => clearTimeout(markerTimer));

  let viewport: HTMLDivElement;
  let availableWidth = 0;
  let availableHeight = 0;
  let zoom: ScreenZoom = 1;
  let lastTap: { at: number; x: number; y: number } | null = null;
  $: size = fitScreen(availableWidth, availableHeight, screen?.width ?? 16, screen?.height ?? 9);
  $: if (!screen || !sharing) fit();

  function fit() {
    zoom = 1;
    lastTap = null;
    viewport?.scrollTo(0, 0);
  }

  function tap(event: MouseEvent) {
    if (pointMode) {
      lastTap = null;
      if (disabledReason || pendingKinds.includes('point')) return;
      const rect = (event.currentTarget as HTMLButtonElement).getBoundingClientRect();
      const point = normalizedPoint(event.detail === 0 ? rect.left + rect.width / 2 : event.clientX,
        event.detail === 0 ? rect.top + rect.height / 2 : event.clientY, rect);
      if (point && onaction('point', point)) {
        marker = point;
        clearTimeout(markerTimer);
        markerTimer = setTimeout(() => { marker = null; }, 3000);
        pointMode = false;
      }
      return;
    }
    const now = performance.now();
    if (event.detail === 0 || (lastTap && now - lastTap.at <= 320 &&
      Math.hypot(event.clientX - lastTap.x, event.clientY - lastTap.y) <= 24)) {
      zoom = toggleScreenZoom(zoom);
      lastTap = null;
      if (zoom === 1) fit();
    } else lastTap = { at: now, x: event.clientX, y: event.clientY };
  }

  onMount(() => {
    const observer = new ResizeObserver(([entry]) => {
      availableWidth = entry.contentRect.width;
      availableHeight = entry.contentRect.height;
      fit();
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  });
</script>

<section class="shared-screen with-helper-actions" aria-label="Shared screen">
  <div class="action-screen-view">
    <div class="screen-viewport" class:zoomed={zoom === 2} bind:this={viewport}>
      {#if screen && sharing}
        <button class="screen-image" class:point-mode={pointMode} onclick={tap}
          aria-label={pointMode ? 'Tap where you want to point; press Enter to point at the center.' : 'Shared screen. Double-tap to zoom or fit; press Enter to toggle.'}
          aria-pressed={zoom === 2} style:width={`${size.width * zoom}px`} style:height={`${size.height * zoom}px`}>
          <img src={screen.url} alt="Your family member's whole shared screen" draggable="false" />
          {#if marker}<span class="helper-point-ring" style:left={`${marker.x * 100}%`} style:top={`${marker.y * 100}%`} aria-hidden="true"></span>{/if}
        </button>
      {:else}
        <p class="screen-placeholder">{sharing ? 'Waiting for the screen' : 'Screen sharing is off'}</p>
      {/if}
    </div>
    {#if zoom === 2}
      <button class="glass fit-screen" onclick={fit}>Fit screen</button>
    {/if}
    {#if sharing && screen && hint}<p class="glass freshness" role="status">{hint}</p>{/if}
  </div>
  <div class="helper-action-bar">
    <nav class="helper-action-buttons" aria-label="Help on the shared screen">
      {#each actionKinds as action}
        <button class="helper-action" class:point-selected={action === 'point' && pointMode}
          disabled={!!disabledReason || pendingKinds.includes(action) || (action === 'point' && !screen)}
          aria-pressed={action === 'point' ? pointMode : undefined} aria-describedby="helper-action-reason"
          onclick={() => {
            if (action === 'point') { pointMode = !pointMode; lastTap = null; }
            else onaction(action);
          }}>
          {actionLabel(action, pendingKinds.includes(action), results[action])}
        </button>
      {/each}
    </nav>
    <p id="helper-action-reason" class="helper-action-reason" role="status" aria-live="polite">
      {disabledReason || (pointMode ? 'Tap where you want to point' : !screen ? 'Waiting for the screen' : 'Every action is announced on their screen')}
    </p>
  </div>
</section>
