import { useEffect, useState, useSyncExternalStore } from "react";

import type { PrepLimits } from "../../lib/image";
import { isActive, type QueueHandlers, type UploadItem, type UploadMeta, UploadQueue } from "./uploadQueue";
import { warnBeforeUnload } from "./useLeaveWarning";

export interface UploadQueueApi {
  items: readonly UploadItem[];
  add: (files: readonly File[], meta: UploadMeta, previewUrls?: readonly string[]) => void;
  retry: (id: string) => void;
  retryAll: () => void;
  remove: (id: string) => void;
}

/**
 * The guest's queues, one per event, kept outside React: leaving the upload
 * page (Back gesture, the Wall link) doesn't cancel photos that are still
 * sending, and coming back shows them again.
 */
const queues = new Map<string, UploadQueue>();
/** Queues finishing in the background, each with the function that stops watching it. */
const background = new Map<UploadQueue, () => void>();

function attach(queue: UploadQueue): void {
  background.get(queue)?.();
  // Also re-registers a queue disposed by an earlier unmount (StrictMode mounts twice).
  queues.set(queue.eventId, queue);
}

/** Nothing shows the queue any more: dispose of it now if idle, or once its last photo has settled. */
function detach(queue: UploadQueue): void {
  const release = () => {
    queue.dispose();
    if (queues.get(queue.eventId) === queue) queues.delete(queue.eventId);
  };
  if (!queue.getSnapshot().some(isActive)) {
    release();
    return;
  }

  // Closing the tab would still cancel the uploads.
  const stopWarning = warnBeforeUnload();
  const unsubscribe = queue.subscribe(() => {
    if (queue.getSnapshot().some(isActive)) return;
    stopWatching();
    release();
  });
  function stopWatching() {
    unsubscribe();
    stopWarning();
    background.delete(queue);
  }
  background.set(queue, stopWatching);
}

/** Cancel and forget every queue (tests start from a clean slate with this). */
export function resetUploadQueues(): void {
  for (const stopWatching of background.values()) stopWatching();
  for (const queue of queues.values()) queue.dispose();
  queues.clear();
}

/**
 * The event's upload queue. It outlives the component: when the page goes
 * away mid-upload the photos keep sending, and the queue is disposed of
 * (previews released) once they have settled.
 */
export function useUploadQueue(eventId: string, limits: PrepLimits, handlers: QueueHandlers): UploadQueueApi {
  const [queue] = useState(() => queues.get(eventId) ?? new UploadQueue(eventId, limits));
  const items = useSyncExternalStore(queue.subscribe, queue.getSnapshot, queue.getSnapshot);

  const { onUploaded, onStopped } = handlers;
  useEffect(() => {
    queue.configure(limits, { onUploaded, onStopped });
  }, [queue, limits, onUploaded, onStopped]);

  useEffect(() => {
    attach(queue);
    return () => detach(queue);
  }, [queue]);

  return { items, add: queue.add, retry: queue.retry, retryAll: queue.retryAll, remove: queue.remove };
}
