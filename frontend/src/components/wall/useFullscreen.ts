import { useCallback, useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  document.addEventListener("fullscreenchange", onChange);
  return () => document.removeEventListener("fullscreenchange", onChange);
}

const isActive = () => document.fullscreenElement != null;

export interface Fullscreen {
  /** False on e.g. iPhone Safari, which only allows fullscreen video. */
  supported: boolean;
  active: boolean;
  toggle: () => Promise<void>;
}

/** Whole-page fullscreen, so dialogs and toasts stay visible on top of the wall. */
export function useFullscreen(): Fullscreen {
  const active = useSyncExternalStore(subscribe, isActive, () => false);
  const supported = document.fullscreenEnabled === true && typeof document.documentElement.requestFullscreen === "function";

  const toggle = useCallback(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen({ navigationUI: "hide" });
  }, []);

  return { supported, active, toggle };
}
