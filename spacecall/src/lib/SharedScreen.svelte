<script lang="ts">
  import { onMount } from 'svelte';
  import type { SharedScreen } from './screenshare';
  import { fitScreen, toggleScreenZoom, type ScreenZoom } from './screenPolicy';

  export let screen: SharedScreen | null = null;
  export let sharing = true;
  export let hint = '';

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

<section class="shared-screen" aria-label="Shared screen">
  <div class="screen-viewport" class:zoomed={zoom === 2} bind:this={viewport}>
    {#if screen && sharing}
      <button class="screen-image" onclick={tap} aria-label="Shared screen. Double-tap to zoom or fit; press Enter to toggle."
        aria-pressed={zoom === 2} style:width={`${size.width * zoom}px`} style:height={`${size.height * zoom}px`}>
        <img src={screen.url} alt="Your family member's whole shared screen" draggable="false" />
      </button>
    {:else}
      <p class="screen-placeholder">{sharing ? 'Waiting for the screen' : 'Screen sharing is off'}</p>
    {/if}
  </div>
  {#if zoom === 2}
    <button class="glass fit-screen" onclick={fit}>Fit screen</button>
  {/if}
  {#if sharing && screen && hint}<p class="glass freshness" role="status">{hint}</p>{/if}
</section>
