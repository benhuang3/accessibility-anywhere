<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { actionLabel, normalizedPoint, type HelperAction, type HelperActionKind, type ActionResults, type Point } from './helperActions';
  import type { SharedScreen } from './screenshare';
  import { fitScreen, toggleScreenZoom, type ScreenZoom } from './screenPolicy';

  export let screen: SharedScreen | null = null;
  export let sharing = true;
  export let hint = '';
  export let disabledReason = '';
  export let pending: Record<number, HelperAction> = {};
  export let results: ActionResults = {};
  export let onaction: (action: HelperActionKind, point?: Point) => boolean = () => false;

  type Mode = 'hl' | 'touch';
  type Stroke = { id: number; points: Point[]; ended: boolean };
  let mode: Mode | null = null;
  let gesture: { id: number; mode: Mode; point: Point; strokeId: number } | null = null;
  let strokes: Stroke[] = [];
  let nextStroke = 0;
  const fadeTimers = new Set<ReturnType<typeof setTimeout>>();
  $: closing = Object.values(pending).some(row => row.action === 'close_tab');
  $: if (disabledReason || !screen || !sharing) { finishGesture(); mode = null; strokes = []; }
  onDestroy(() => {
    finishGesture();
    for (const timer of fadeTimers) clearTimeout(timer);
  });

  function selectMode(next: Mode) {
    finishGesture();
    mode = mode === next ? null : next;
    lastTap = null;
  }

  function gesturePoint(event: PointerEvent): Point | null {
    const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return normalizedPoint(Math.max(rect.left, Math.min(rect.right, event.clientX)),
      Math.max(rect.top, Math.min(rect.bottom, event.clientY)), rect);
  }

  function startGesture(event: PointerEvent) {
    if (!mode || gesture || disabledReason || !event.isPrimary || event.button !== 0) return;
    const point = gesturePoint(event);
    if (!point || !onaction(`${mode}_start`, point)) return;
    event.preventDefault();
    gesture = { id: event.pointerId, mode, point, strokeId: ++nextStroke };
    if (mode === 'hl') strokes = [...strokes, { id: nextStroke, points: [point], ended: false }];
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    lastTap = null;
  }

  function moveGesture(event: PointerEvent) {
    if (!gesture || gesture.id !== event.pointerId) return;
    const point = gesturePoint(event);
    if (!point) return;
    event.preventDefault();
    gesture.point = point;
    if (gesture.mode === 'hl') strokes = strokes.map(stroke => stroke.id === gesture?.strokeId
      ? { ...stroke, points: [...stroke.points, point] } : stroke);
    onaction(`${gesture.mode}_move`, point);
  }

  function finishGesture(event?: PointerEvent) {
    if (!gesture || (event && gesture.id !== event.pointerId)) return;
    const current = gesture;
    const point = event ? gesturePoint(event) ?? current.point : current.point;
    gesture = null;
    onaction(`${current.mode}_end`, point);
    if (current.mode === 'hl') {
      strokes = strokes.map(stroke => stroke.id === current.strokeId
        ? { ...stroke, points: [...stroke.points, point], ended: true } : stroke);
      const timer = setTimeout(() => {
        strokes = strokes.filter(stroke => stroke.id !== current.strokeId);
        fadeTimers.delete(timer);
      }, 2500);
      fadeTimers.add(timer);
    }
  }

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
    if (mode) return;
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
        <button class="screen-image" class:input-mode={mode !== null} onclick={tap}
          onpointerdown={startGesture} onpointermove={moveGesture} onpointerup={finishGesture}
          onpointercancel={finishGesture} onlostpointercapture={() => finishGesture()}
          aria-label={mode === 'hl' ? 'Drag to highlight their screen' : mode === 'touch' ? 'Tap to click; drag to scroll their screen' : 'Shared screen. Double-tap to zoom or fit; press Enter to toggle.'}
          aria-pressed={zoom === 2} style:width={`${size.width * zoom}px`} style:height={`${size.height * zoom}px`}>
          <img src={screen.url} alt="Your family member's whole shared screen" draggable="false" />
          <svg class="helper-marker" viewBox={`0 0 ${size.width * zoom} ${size.height * zoom}`} aria-hidden="true">
            {#each strokes as stroke (stroke.id)}
              <g class:stroke-ended={stroke.ended}>
                <polyline points={stroke.points.map(p => `${p.x * size.width * zoom},${p.y * size.height * zoom}`).join(' ')} />
                <circle class="marker-tip" cx={stroke.points[stroke.points.length - 1].x * size.width * zoom}
                  cy={stroke.points[stroke.points.length - 1].y * size.height * zoom} r="4" />
              </g>
            {/each}
          </svg>
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
      <button class="helper-action" class:mode-selected={mode === 'hl'}
        disabled={!!disabledReason || !screen || !sharing} aria-pressed={mode === 'hl'}
        aria-describedby="helper-action-reason" onclick={() => selectMode('hl')}>Highlight</button>
      <button class="helper-action" class:mode-selected={mode === 'touch'}
        disabled={!!disabledReason || !screen || !sharing} aria-pressed={mode === 'touch'}
        aria-describedby="helper-action-reason" onclick={() => selectMode('touch')}>Use their screen</button>
      <button class="helper-action" disabled={!!disabledReason || closing || !sharing}
        aria-describedby="helper-action-reason" onclick={() => onaction('close_tab')}>
        {actionLabel('close_tab', closing, results.close_tab)}
      </button>
    </nav>
    <p id="helper-action-reason" class="helper-action-reason" role="status" aria-live="polite">
      {disabledReason || (mode === 'hl' ? 'Drag to highlight their screen' : mode === 'touch' ? 'Tap to click; drag to scroll' : !screen ? 'Waiting for the screen' : 'Choose how to help on their screen')}
    </p>
  </div>
</section>
