import { useEffect } from "react";

/**
 * Ask the browser to confirm before the page is closed or reloaded (which
 * would cancel the uploads). Returns the function that stops asking.
 */
export function warnBeforeUnload(): () => void {
  // A new listener per call: the same function added twice would only be registered once.
  const onBeforeUnload = (event: BeforeUnloadEvent) => {
    event.preventDefault();
    event.returnValue = ""; // still required by some browsers to show the prompt
  };
  window.addEventListener("beforeunload", onBeforeUnload);
  return () => window.removeEventListener("beforeunload", onBeforeUnload);
}

/**
 * While ``enabled``, warn before the page is closed or reloaded. In-app
 * navigation is left alone: the upload queue keeps sending in the background.
 */
export function useLeaveWarning(enabled: boolean): void {
  useEffect(() => (enabled ? warnBeforeUnload() : undefined), [enabled]);
}
