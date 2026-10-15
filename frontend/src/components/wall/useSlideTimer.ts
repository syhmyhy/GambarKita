import { type RefObject, useEffect, useEffectEvent, useRef } from "react";

interface SlideTimerOptions {
  /** Changes on every new slide, which restarts the countdown. */
  slideKey: number;
  duration: number;
  paused: boolean;
  onElapsed: () => void;
  /**
   * Optional element animated from scaleX(0) to scaleX(1) as a progress bar.
   * Not animated when the viewer prefers reduced motion (slides still advance).
   */
  barRef?: RefObject<HTMLElement | null>;
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Countdown to the next slide. Pausing keeps the remaining time (and the bar's position). */
export function useSlideTimer({ slideKey, duration, paused, onElapsed, barRef }: SlideTimerOptions): void {
  const remaining = useRef(duration);
  const animation = useRef<Animation | null>(null);
  const elapsed = useEffectEvent(onElapsed);

  useEffect(() => {
    remaining.current = duration;
    const bar = barRef?.current;
    const running =
      bar && typeof bar.animate === "function" && !prefersReducedMotion()
        ? bar.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration, easing: "linear", fill: "forwards" })
        : null;
    animation.current = running;
    return () => {
      running?.cancel();
      animation.current = null;
    };
  }, [slideKey, duration, barRef]);

  useEffect(() => {
    if (paused) {
      animation.current?.pause();
      return;
    }
    animation.current?.play();
    const startedAt = Date.now();
    const timer = window.setTimeout(() => elapsed(), remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt));
    };
  }, [paused, slideKey]);
}
