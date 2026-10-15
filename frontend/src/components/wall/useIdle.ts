import { useEffect, useState } from "react";

const ACTIVITY_EVENTS = ["pointermove", "pointerdown", "keydown", "wheel", "touchstart"] as const;

/**
 * True once there has been no pointer or keyboard activity for ``timeout`` ms
 * while ``enabled``. Activity is tracked even while disabled, so turning it on
 * never starts out stale.
 */
export function useIdle(timeout: number, enabled: boolean): boolean {
  const [idle, setIdle] = useState(false);

  useEffect(() => {
    let timer: number | undefined;
    const arm = () => {
      window.clearTimeout(timer);
      if (enabled) timer = window.setTimeout(() => setIdle(true), timeout);
    };
    const onActivity = () => {
      setIdle(false);
      arm();
    };
    arm();
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, onActivity, { passive: true });
    return () => {
      window.clearTimeout(timer);
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, onActivity);
    };
  }, [enabled, timeout]);

  return enabled && idle;
}
