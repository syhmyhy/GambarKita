import { type TouchEvent, useRef } from "react";

const MIN_DISTANCE = 50;

/** Horizontal one-finger swipe handlers; vertical scrolls and pinches are ignored. */
export function useSwipe(onSwipeLeft: () => void, onSwipeRight: () => void) {
  const start = useRef<{ x: number; y: number } | null>(null);

  return {
    onTouchStart: (event: TouchEvent) => {
      const touch = event.touches[0];
      start.current = touch && event.touches.length === 1 ? { x: touch.clientX, y: touch.clientY } : null;
    },
    onTouchEnd: (event: TouchEvent) => {
      const origin = start.current;
      const touch = event.changedTouches[0];
      start.current = null;
      if (!origin || !touch) return;
      const dx = touch.clientX - origin.x;
      const dy = touch.clientY - origin.y;
      if (Math.abs(dx) < MIN_DISTANCE || Math.abs(dx) < Math.abs(dy) * 1.5) return;
      if (dx < 0) onSwipeLeft();
      else onSwipeRight();
    },
    onTouchCancel: () => {
      start.current = null;
    },
  };
}
