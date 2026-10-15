import { ApiError } from "../../lib/api";
import { describeError } from "../../lib/errors";
import { PhotoPrepError, type PreparedPhoto, type PrepLimits, preparePhoto } from "../../lib/image";
import type { Photo } from "../../lib/types";
import { uploadPhoto } from "../../lib/upload";

export type UploadStatus = "preparing" | "waiting" | "uploading" | "done" | "error";

export interface UploadFailure {
  title: string;
  message: string;
}

export interface UploadItem {
  readonly id: string;
  readonly file: File;
  /** Object URL of the original file, for the thumbnail. Revoked when the item goes away. */
  readonly previewUrl: string;
  readonly uploaderName: string;
  readonly caption: string;
  readonly status: UploadStatus;
  /** Fraction of the request body sent, 0..1. */
  readonly progress: number;
  readonly error: UploadFailure | null;
  readonly retryable: boolean;
  /** Waiting out a rate limit / busy server before the one automatic retry. */
  readonly autoRetrying: boolean;
}

export interface UploadMeta {
  uploaderName: string;
  caption: string;
}

export interface QueueHandlers {
  onUploaded?: (photo: Photo) => void;
  /** An error after which nothing else in the queue can succeed (uploads closed, event gone or full, guest limit reached). */
  onStopped?: (error: ApiError) => void;
}

export const MAX_PARALLEL_UPLOADS = 2;
export const MAX_FILES_PER_SELECTION = 20;
const DEFAULT_RETRY_DELAY_SECONDS = 5;
const MAX_RETRY_DELAY_SECONDS = 60;

const AUTO_RETRY_CODES = new Set(["RATE_LIMITED", "SERVICE_BUSY"]);
const QUEUE_STOPPING_CODES = new Set(["UPLOADS_CLOSED", "EVENT_NOT_FOUND", "EVENT_PHOTO_LIMIT", "GUEST_PHOTO_LIMIT"]);

export function isActive(item: Pick<UploadItem, "status">): boolean {
  return item.status === "waiting" || item.status === "preparing" || item.status === "uploading";
}

function isRunning(item: Pick<UploadItem, "status">): boolean {
  return item.status === "preparing" || item.status === "uploading";
}

export function describeUploadFailure(error: unknown): UploadFailure & { retryable: boolean } {
  // Preparation failures (unreadable HEIC, not an image...) won't fix themselves on retry.
  if (error instanceof PhotoPrepError) {
    return { title: "Can't use this photo", message: error.message, retryable: false };
  }
  const { title, message, retryable } = describeError(error);
  return { title, message, retryable };
}

export interface QueueSummary {
  total: number;
  done: number;
  failed: number;
  /** Failed items that can be retried. */
  retryable: number;
  /** Waiting, preparing or uploading. */
  active: number;
  /** Overall progress of the queue, 0..1. */
  progress: number;
  allDone: boolean;
}

export function summarizeQueue(items: readonly UploadItem[]): QueueSummary {
  let done = 0;
  let failed = 0;
  let retryable = 0;
  let active = 0;
  let sent = 0;
  for (const item of items) {
    if (item.status === "done") {
      done += 1;
      sent += 1;
    } else if (item.status === "error") {
      failed += 1;
      if (item.retryable) retryable += 1;
    } else {
      active += 1;
      if (item.status === "uploading") sent += item.progress;
    }
  }
  const total = items.length;
  return {
    total,
    done,
    failed,
    retryable,
    active,
    progress: total === 0 ? 0 : sent / total,
    allDone: total > 0 && done === total,
  };
}

/**
 * The guest's upload queue: prepares (shrinks/converts) and uploads photos, at
 * most MAX_PARALLEL_UPLOADS at a time, so a phone on party Wi-Fi isn't asked
 * to push 20 photos at once. Framework-agnostic; React reads it through
 * ``subscribe``/``getSnapshot`` (useSyncExternalStore).
 */
export class UploadQueue {
  private items: readonly UploadItem[] = [];
  private readonly listeners = new Set<() => void>();
  private readonly controllers = new Map<string, AbortController>();
  /** Prepared blobs are kept until the upload succeeds, so a retry doesn't re-encode. */
  private readonly prepared = new Map<string, PreparedPhoto>();
  private readonly autoRetried = new Set<string>();
  /**
   * Items the server turned away for their pixel dimensions (a panorama, a
   * long screenshot): prepared again, decoded and shrunk by the browser.
   */
  private readonly shrink = new Set<string>();
  /** While set, the queue is paused after a rate limit / busy server. */
  private resumeTimer: ReturnType<typeof setTimeout> | null = null;
  readonly eventId: string;
  private limits: PrepLimits;
  private handlers: QueueHandlers = {};
  private nextId = 1;

  constructor(eventId: string, limits: PrepLimits) {
    this.eventId = eventId;
    this.limits = limits;
  }

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  readonly getSnapshot = (): readonly UploadItem[] => this.items;

  configure(limits: PrepLimits, handlers: QueueHandlers): void {
    this.limits = limits;
    this.handlers = handlers;
  }

  /**
   * Queue ``files`` for upload. ``previewUrls`` (same order) hands over previews the
   * page already made, e.g. while the guest confirmed; the queue then owns and releases them.
   */
  readonly add = (files: readonly File[], meta: UploadMeta, previewUrls?: readonly string[]): void => {
    if (files.length === 0) return;
    let kept = this.items;
    if (!kept.some(isActive)) {
      // The previous batch has finished: start a fresh grid (failed items stay).
      for (const item of kept) if (item.status === "done") URL.revokeObjectURL(item.previewUrl);
      kept = kept.filter((item) => item.status !== "done");
    }
    const added = files.map(
      (file, index): UploadItem => ({
        id: `upload-${this.nextId++}`,
        file,
        previewUrl: previewUrls?.[index] ?? URL.createObjectURL(file),
        uploaderName: meta.uploaderName,
        caption: meta.caption,
        status: "waiting",
        progress: 0,
        error: null,
        retryable: false,
        autoRetrying: false,
      }),
    );
    this.items = [...kept, ...added];
    this.emit();
    this.pump();
  };

  readonly retry = (id: string): void => {
    const item = this.find(id);
    if (!item || item.status !== "error") return;
    this.autoRetried.delete(id);
    this.patch(id, { status: "waiting", progress: 0, error: null, retryable: false });
    this.pump();
  };

  readonly retryAll = (): void => {
    const ids = new Set(this.items.filter((item) => item.status === "error" && item.retryable).map((item) => item.id));
    if (ids.size === 0) return;
    for (const id of ids) this.autoRetried.delete(id);
    this.items = this.items.map((item) =>
      ids.has(item.id) ? { ...item, status: "waiting", progress: 0, error: null, retryable: false } : item,
    );
    this.emit();
    this.pump();
  };

  /** Remove an item, cancelling its upload if it is still running. */
  readonly remove = (id: string): void => {
    const item = this.find(id);
    if (!item) return;
    this.controllers.get(id)?.abort();
    this.controllers.delete(id);
    this.prepared.delete(id);
    this.autoRetried.delete(id);
    this.shrink.delete(id);
    URL.revokeObjectURL(item.previewUrl);
    this.items = this.items.filter((other) => other.id !== id);
    this.emit();
    this.pump();
  };

  /** Cancel everything and release all previews (once nothing shows the queue). It stays usable, empty. */
  readonly dispose = (): void => {
    for (const controller of this.controllers.values()) controller.abort();
    this.controllers.clear();
    this.prepared.clear();
    this.autoRetried.clear();
    this.shrink.clear();
    if (this.resumeTimer !== null) clearTimeout(this.resumeTimer);
    this.resumeTimer = null;
    for (const item of this.items) URL.revokeObjectURL(item.previewUrl);
    this.items = [];
    this.emit();
  };

  private find(id: string): UploadItem | undefined {
    return this.items.find((item) => item.id === id);
  }

  private emit(): void {
    for (const listener of this.listeners) listener();
  }

  private patch(id: string, changes: Partial<UploadItem>): void {
    let changed = false;
    this.items = this.items.map((item) => {
      if (item.id !== id) return item;
      changed = true;
      return { ...item, ...changes };
    });
    if (changed) this.emit();
  }

  /** Start waiting items while there are free slots. */
  private pump(): void {
    if (this.resumeTimer !== null) return;
    let running = this.items.filter(isRunning).length;
    for (const item of this.items) {
      if (running >= MAX_PARALLEL_UPLOADS) return;
      if (item.status !== "waiting") continue;
      running += 1;
      void this.run(item);
    }
  }

  private async run(item: UploadItem): Promise<void> {
    const { id } = item;
    const controller = new AbortController();
    const { signal } = controller;
    this.controllers.set(id, controller);
    try {
      // Both branches update the status synchronously, before the first await,
      // so pump() never starts the same item twice.
      let prepared = this.prepared.get(id);
      if (!prepared) {
        this.patch(id, { status: "preparing", progress: 0, autoRetrying: false });
        // No allowed types forces a decode and resize (to MAX_EDGE), whatever the file's type and size.
        const limits = this.shrink.has(id) ? { ...this.limits, allowedTypes: [] } : this.limits;
        prepared = await preparePhoto(item.file, limits);
        if (signal.aborted) return;
        this.prepared.set(id, prepared);
      }
      this.patch(id, { status: "uploading", progress: 0, autoRetrying: false });
      const photo = await uploadPhoto(this.eventId, prepared.blob, prepared.filename, {
        uploaderName: item.uploaderName,
        caption: item.caption,
        onProgress: (fraction) => this.setProgress(id, fraction),
        signal,
      });
      if (signal.aborted) return;
      this.prepared.delete(id);
      this.shrink.delete(id);
      this.patch(id, { status: "done", progress: 1 });
      this.handlers.onUploaded?.(photo);
    } catch (error) {
      if (signal.aborted) return;
      this.fail(id, error);
    } finally {
      if (this.controllers.get(id) === controller) this.controllers.delete(id);
      if (!signal.aborted) this.pump();
    }
  }

  private setProgress(id: string, fraction: number): void {
    const item = this.find(id);
    if (!item || item.status !== "uploading") return;
    const progress = Math.min(1, Math.max(0, fraction));
    // XHR progress fires very often; only re-render for visible changes.
    if (progress < 1 && progress - item.progress < 0.01) return;
    this.patch(id, { progress });
  }

  private fail(id: string, error: unknown): void {
    if (error instanceof ApiError && error.code === "IMAGE_TOO_LARGE" && this.canShrink(id)) {
      // Back in line; the next pump() prepares it again, shrunk.
      this.shrink.add(id);
      this.prepared.delete(id);
      this.patch(id, { status: "waiting", progress: 0, autoRetrying: false });
      return;
    }

    if (error instanceof ApiError && AUTO_RETRY_CODES.has(error.code) && !this.autoRetried.has(id)) {
      this.autoRetried.add(id);
      this.patch(id, { status: "waiting", progress: 0, autoRetrying: true });
      this.pauseFor(error.retryAfter ?? DEFAULT_RETRY_DELAY_SECONDS);
      return;
    }

    const { retryable, ...failure } = describeUploadFailure(error);
    this.patch(id, { status: "error", progress: 0, error: failure, retryable, autoRetrying: false });
    if (error instanceof ApiError && QUEUE_STOPPING_CODES.has(error.code)) this.stop(error, failure);
  }

  /**
   * Whether a photo with too many pixels can be sent once more, shrunk in the
   * browser. Not when that was already tried, or when the browser had already
   * resized it: shrinking again would send the same photo.
   */
  private canShrink(id: string): boolean {
    return !this.shrink.has(id) && !this.prepared.get(id)?.converted;
  }

  /** Pause the whole queue: the other photos would hit the same limit. */
  private pauseFor(seconds: number): void {
    const delay = Math.min(Math.max(seconds, 1), MAX_RETRY_DELAY_SECONDS) * 1000;
    if (this.resumeTimer !== null) clearTimeout(this.resumeTimer);
    this.resumeTimer = setTimeout(() => {
      this.resumeTimer = null;
      this.pump();
    }, delay);
  }

  /** Fail everything still pending at once instead of sending each photo into the same error. */
  private stop(error: ApiError, failure: UploadFailure): void {
    for (const item of this.items) {
      if (!isActive(item)) continue;
      this.controllers.get(item.id)?.abort();
      this.controllers.delete(item.id);
    }
    this.items = this.items.map((item) =>
      isActive(item) ? { ...item, status: "error", progress: 0, error: failure, retryable: false, autoRetrying: false } : item,
    );
    this.emit();
    this.handlers.onStopped?.(error);
  }
}
