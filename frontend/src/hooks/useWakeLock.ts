import { useEffect } from "react";

/** Keep the screen awake (e.g. a photo wall on a TV or projector) while ``enabled``. */
export function useWakeLock(enabled: boolean): void {
  useEffect(() => {
    if (!enabled || !("wakeLock" in navigator)) return;
    let sentinel: WakeLockSentinel | null = null;
    let cancelled = false;

    const acquire = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        sentinel = await navigator.wakeLock.request("screen");
        if (cancelled) void sentinel.release();
      } catch {
        // Not allowed (e.g. battery saver): the wall still works, the screen may sleep.
      }
    };
    // The lock is dropped whenever the tab is hidden; take it again on return.
    const onVisibility = () => void acquire();

    void acquire();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      void sentinel?.release().catch(() => undefined);
    };
  }, [enabled]);
}
