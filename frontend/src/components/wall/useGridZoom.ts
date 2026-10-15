import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

import { wallColumns } from "../../lib/storage";

export const MIN_COLUMNS = 1;
/** Fingers must spread (or close) this much more before the next step, like iPhone Photos. */
const PINCH_STEP = 1.3;
/** Wheel distance (Ctrl + mouse wheel, trackpad pinch) per step: one mouse notch is about 100. */
const WHEEL_STEP = 50;

/** Most columns that fit: tiles stay at least ~64 px wide. */
export function maxColumns(width: number): number {
  return Math.max(3, Math.min(12, Math.floor(width / 64)));
}

/** The automatic layout before anyone zooms: 2 / 3 / 4 / 5 columns at Tailwind's base / sm / lg / 2xl. */
export function defaultColumns(width: number): number {
  if (width >= 1536) return 5;
  if (width >= 1024) return 4;
  if (width >= 640) return 3;
  return 2;
}

function subscribeToResize(onChange: () => void) {
  window.addEventListener("resize", onChange);
  return () => window.removeEventListener("resize", onChange);
}

function useWindowWidth(): number {
  return useSyncExternalStore(
    subscribeToResize,
    () => window.innerWidth,
    () => 1024,
  );
}

interface Point {
  x: number;
  y: number;
}

/** The tile under ``point`` (or the first one on screen) and where it is, to keep it in place across a zoom. */
function findAnchor(point?: Point): { id: string; top: number } | null {
  let tile: Element | null | undefined;
  if (point && typeof document.elementFromPoint === "function") {
    tile = document.elementFromPoint(point.x, point.y)?.closest("[data-photo-id]");
  }
  tile ??= Array.from(document.querySelectorAll("[data-photo-id]")).find((element) => element.getBoundingClientRect().bottom > 0);
  const id = tile?.getAttribute("data-photo-id");
  return tile && id ? { id, top: tile.getBoundingClientRect().top } : null;
}

export interface GridZoom {
  columns: number;
  /** +1 = one more column (smaller photos), -1 = one fewer (bigger photos). */
  zoom: (delta: number, at?: Point) => void;
  canZoomIn: boolean;
  canZoomOut: boolean;
}

/**
 * The live wall's column count, changed by pinching like iPhone Photos (see ``usePinchZoom``)
 * or with the zoom buttons. Remembered per device; follows the screen size until first changed.
 */
export function useGridZoom(): GridZoom {
  const width = useWindowWidth();
  const [chosen, setChosen] = useState<number | null>(wallColumns.get);
  const max = maxColumns(width);
  const columns = Math.min(Math.max(chosen ?? defaultColumns(width), MIN_COLUMNS), max);

  // Read by gesture handlers, which can step several times before React re-renders.
  const columnsRef = useRef(columns);
  const anchor = useRef<{ id: string; top: number } | null>(null);
  useEffect(() => {
    columnsRef.current = columns;
  }, [columns]);

  const zoom = useCallback((delta: number, at?: Point) => {
    const current = columnsRef.current;
    const next = Math.min(Math.max(current + delta, MIN_COLUMNS), maxColumns(window.innerWidth));
    if (next === current) return;
    anchor.current ??= findAnchor(at);
    columnsRef.current = next;
    setChosen(next);
    wallColumns.set(next);
  }, []);

  // Keep the photo that was under the fingers (or at the top) where it was, as iPhone Photos does.
  useLayoutEffect(() => {
    const kept = anchor.current;
    anchor.current = null;
    if (!kept) return;
    const tile = document.querySelector(`[data-photo-id="${kept.id}"]`);
    if (tile) window.scrollBy(0, tile.getBoundingClientRect().top - kept.top);
  }, [columns]);

  return { columns, zoom, canZoomIn: columns > MIN_COLUMNS, canZoomOut: columns < max };
}

/** Safari's own pinch events (iOS and Mac trackpads); not in TypeScript's DOM types. */
interface GestureEvent extends UIEvent {
  scale: number;
  clientX: number;
  clientY: number;
}

/**
 * Two-finger pinch (phones and tablets), trackpad pinch and Ctrl + mouse wheel on ``target``
 * call ``zoom``: spreading the fingers gives fewer, bigger photos. The page itself doesn't zoom.
 */
export function usePinchZoom(target: RefObject<HTMLElement | null>, zoom: GridZoom["zoom"]): void {
  useEffect(() => {
    const element = target.current;
    if (!element) return;

    let baseDistance = 0; // 0 = no two-finger touch in progress
    let baseScale = 1;
    let wheel = 0;

    const distance = (touches: TouchList) => {
      const [a, b] = [touches[0], touches[1]];
      return a && b ? Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY) : 0;
    };
    const midpoint = (touches: TouchList): Point | undefined => {
      const [a, b] = [touches[0], touches[1]];
      return a && b ? { x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2 } : undefined;
    };
    const stepFor = (ratio: number) => (ratio >= PINCH_STEP ? -1 : ratio <= 1 / PINCH_STEP ? 1 : 0);

    const onTouchStart = (event: TouchEvent) => {
      baseDistance = event.touches.length === 2 ? distance(event.touches) : 0;
    };
    const onTouchMove = (event: TouchEvent) => {
      if (event.touches.length !== 2 || baseDistance === 0) return;
      event.preventDefault(); // pinch the grid, not the page
      const now = distance(event.touches);
      const step = stepFor(now / baseDistance);
      if (step !== 0) {
        zoom(step, midpoint(event.touches));
        baseDistance = now;
      }
    };
    const onTouchEnd = (event: TouchEvent) => {
      if (event.touches.length < 2) baseDistance = 0;
    };

    const onGestureStart = (event: Event) => {
      event.preventDefault();
      baseScale = 1;
    };
    const onGestureChange = (event: Event) => {
      event.preventDefault();
      // On iPhones the touch events above already handle it; this is for Mac trackpads in Safari.
      if (baseDistance !== 0) return;
      const gesture = event as GestureEvent;
      const step = stepFor(gesture.scale / baseScale);
      if (step !== 0) {
        zoom(step, { x: gesture.clientX, y: gesture.clientY });
        baseScale = gesture.scale;
      }
    };

    // Chrome, Edge and Firefox report a trackpad pinch as a wheel event with ctrlKey.
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      wheel += event.deltaY;
      if (Math.abs(wheel) >= WHEEL_STEP) {
        zoom(wheel > 0 ? 1 : -1, { x: event.clientX, y: event.clientY });
        wheel = 0;
      }
    };

    element.addEventListener("touchstart", onTouchStart, { passive: true });
    element.addEventListener("touchmove", onTouchMove, { passive: false });
    element.addEventListener("touchend", onTouchEnd);
    element.addEventListener("touchcancel", onTouchEnd);
    element.addEventListener("gesturestart", onGestureStart);
    element.addEventListener("gesturechange", onGestureChange);
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      element.removeEventListener("touchstart", onTouchStart);
      element.removeEventListener("touchmove", onTouchMove);
      element.removeEventListener("touchend", onTouchEnd);
      element.removeEventListener("touchcancel", onTouchEnd);
      element.removeEventListener("gesturestart", onGestureStart);
      element.removeEventListener("gesturechange", onGestureChange);
      element.removeEventListener("wheel", onWheel);
    };
  }, [target, zoom]);
}
