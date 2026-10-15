import { useEffect, useState } from "react";

/** Re-render every ``intervalMs`` so relative times ("5 minutes ago") stay current. */
export function useTicker(intervalMs: number): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setTick((tick) => tick + 1), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
}
