import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../lib/api";
import { PhotoPrepError, preparePhoto } from "../../../lib/image";
import type { Photo } from "../../../lib/types";
import { type UploadOptions, uploadPhoto } from "../../../lib/upload";
import { MAX_PARALLEL_UPLOADS, summarizeQueue, UploadQueue } from "../uploadQueue";

vi.mock("../../../lib/image", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../lib/image")>()),
  preparePhoto: vi.fn(),
}));
vi.mock("../../../lib/upload", () => ({ uploadPhoto: vi.fn() }));

const EVENT_ID = "abcdef123456";
const LIMITS = { maxBytes: 10 * 1024 * 1024, allowedTypes: ["image/jpeg"] };
const META = { uploaderName: "Aisyah", caption: "Congrats!" };

interface PendingUpload {
  blob: Blob;
  options: UploadOptions;
  resolve: (photo: Photo) => void;
  reject: (error: unknown) => void;
}

let pending: PendingUpload[];

function photo(id: string): Photo {
  return {
    id,
    event_id: EVENT_ID,
    url: "",
    thumbnail_url: "",
    content_type: "image/jpeg",
    width: 10,
    height: 10,
    size_bytes: 1,
    uploader_name: null,
    caption: null,
    created_at: "2026-09-28T12:00:00Z",
  };
}

function files(count: number): File[] {
  return Array.from({ length: count }, (_, index) => new File(["x"], `photo-${index + 1}.jpg`, { type: "image/jpeg" }));
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

function statuses(queue: UploadQueue) {
  return queue.getSnapshot().map((item) => item.status);
}

function upload(index: number): PendingUpload {
  const item = pending[index];
  if (!item) throw new Error(`No upload #${index}`);
  return item;
}

beforeEach(() => {
  pending = [];
  Object.assign(URL, {
    createObjectURL: vi.fn((file: File) => `blob:${file.name}`),
    revokeObjectURL: vi.fn(),
  });
  vi.mocked(preparePhoto).mockImplementation(async (file) => ({ blob: file, filename: file.name, converted: false }));
  vi.mocked(uploadPhoto).mockImplementation(
    (_eventId, blob, _filename, options = {}) =>
      new Promise<Photo>((resolve, reject) => pending.push({ blob, options, resolve, reject })),
  );
});

afterEach(() => {
  vi.useRealTimers();
});

describe("UploadQueue", () => {
  it(`uploads at most ${MAX_PARALLEL_UPLOADS} photos at a time, in order`, async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    const onUploaded = vi.fn();
    queue.configure(LIMITS, { onUploaded });
    queue.add(files(3), META);
    await flush();

    expect(statuses(queue)).toEqual(["uploading", "uploading", "waiting"]);
    expect(pending).toHaveLength(2);
    expect(upload(0).options).toMatchObject({ uploaderName: "Aisyah", caption: "Congrats!" });

    upload(0).resolve(photo("p1"));
    await flush();
    expect(statuses(queue)).toEqual(["done", "uploading", "uploading"]);
    expect(onUploaded).toHaveBeenCalledWith(photo("p1"));

    upload(1).resolve(photo("p2"));
    upload(2).resolve(photo("p3"));
    await flush();
    expect(summarizeQueue(queue.getSnapshot())).toMatchObject({ total: 3, done: 3, allDone: true, active: 0 });
  });

  it("reports progress and throttles tiny changes", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();
    const before = queue.getSnapshot();
    upload(0).options.onProgress?.(0.001);
    expect(queue.getSnapshot()).toBe(before);
    upload(0).options.onProgress?.(0.42);
    expect(queue.getSnapshot()[0]?.progress).toBe(0.42);
    expect(summarizeQueue(queue.getSnapshot()).progress).toBeCloseTo(0.42);
  });

  it("retries once automatically after Retry-After when rate limited, pausing the queue", async () => {
    vi.useFakeTimers();
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();

    upload(0).reject(new ApiError({ status: 429, code: "RATE_LIMITED", message: "Slow down", retryAfter: 3 }));
    await flush();
    expect(queue.getSnapshot()[0]).toMatchObject({ status: "waiting", autoRetrying: true });

    await vi.advanceTimersByTimeAsync(2900);
    expect(pending).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(pending).toHaveLength(2);
    expect(queue.getSnapshot()[0]).toMatchObject({ status: "uploading", autoRetrying: false });

    // A second rate limit is shown to the guest instead of retrying forever.
    upload(1).reject(new ApiError({ status: 503, code: "SERVICE_BUSY", message: "Busy" }));
    await flush();
    expect(queue.getSnapshot()[0]).toMatchObject({ status: "error", retryable: true });
    expect(queue.getSnapshot()[0]?.error?.title).toBe("The server is busy");
  });

  it("stops the whole queue when uploads are closed", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    const onStopped = vi.fn();
    queue.configure(LIMITS, { onStopped });
    queue.add(files(3), META);
    await flush();

    const closed = new ApiError({ status: 403, code: "UPLOADS_CLOSED", message: "Closed" });
    upload(0).reject(closed);
    await flush();

    expect(statuses(queue)).toEqual(["error", "error", "error"]);
    expect(queue.getSnapshot().every((item) => !item.retryable)).toBe(true);
    expect(upload(1).options.signal?.aborted).toBe(true);
    expect(pending).toHaveLength(2);
    expect(onStopped).toHaveBeenCalledTimes(1);
    expect(onStopped).toHaveBeenCalledWith(closed);
  });

  it("stops the whole queue when the guest reaches their photo limit", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    const onStopped = vi.fn();
    queue.configure(LIMITS, { onStopped });
    queue.add(files(3), META);
    await flush();

    const reached = new ApiError({
      status: 409,
      code: "GUEST_PHOTO_LIMIT",
      message: "Each guest can share up to 2 photos at this event.",
      details: { max_photos_per_guest: 2 },
    });
    upload(0).resolve(photo("p1"));
    await flush();
    expect(pending).toHaveLength(3);
    upload(1).reject(reached);
    await flush();

    expect(statuses(queue)).toEqual(["done", "error", "error"]);
    expect(queue.getSnapshot()[1]?.error?.title).toBe("You've shared all your photos");
    expect(queue.getSnapshot().every((item) => item.status === "done" || !item.retryable)).toBe(true);
    // The third photo was cancelled instead of being sent into the same refusal.
    expect(upload(2).options.signal?.aborted).toBe(true);
    expect(onStopped).toHaveBeenCalledTimes(1);
    expect(onStopped).toHaveBeenCalledWith(reached);
  });

  it("marks preparation failures as not retryable", async () => {
    vi.mocked(preparePhoto).mockRejectedValueOnce(new PhotoPrepError("This browser can't read HEIC photos."));
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();
    expect(queue.getSnapshot()[0]).toMatchObject({
      status: "error",
      retryable: false,
      error: { title: "Can't use this photo", message: "This browser can't read HEIC photos." },
    });
    expect(uploadPhoto).not.toHaveBeenCalled();
  });

  it("reuses the prepared photo when retrying", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();
    upload(0).reject(new ApiError({ status: 0, code: "NETWORK_ERROR", message: "Offline" }));
    await flush();
    const [failed] = queue.getSnapshot();
    expect(failed).toMatchObject({ status: "error", retryable: true });

    queue.retry(failed?.id ?? "");
    await flush();
    expect(pending).toHaveLength(2);
    expect(preparePhoto).toHaveBeenCalledTimes(1);
  });

  it("shrinks a photo in the browser and sends it once more when the server finds it too large", async () => {
    const shrunk = new Blob(["small"], { type: "image/jpeg" });
    vi.mocked(preparePhoto).mockImplementation(async (file, limits) =>
      limits.allowedTypes.length === 0
        ? { blob: shrunk, filename: "panorama.jpg", converted: true }
        : { blob: file, filename: file.name, converted: false },
    );
    const tooLarge = () =>
      new ApiError({ status: 413, code: "IMAGE_TOO_LARGE", message: "The image dimensions are too large." });
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();

    upload(0).reject(tooLarge());
    await flush();
    // Decoded and resized whatever its type and size: no allowed types forces the conversion.
    expect(preparePhoto).toHaveBeenLastCalledWith(queue.getSnapshot()[0]?.file, { ...LIMITS, allowedTypes: [] });
    expect(pending).toHaveLength(2);
    expect(upload(1).blob).toBe(shrunk);
    expect(queue.getSnapshot()[0]).toMatchObject({ status: "uploading", error: null });

    // Only once: a second refusal is shown as usual.
    upload(1).reject(tooLarge());
    await flush();
    expect(pending).toHaveLength(2);
    expect(preparePhoto).toHaveBeenCalledTimes(2);
    expect(queue.getSnapshot()[0]).toMatchObject({
      status: "error",
      retryable: false,
      error: { title: "Photo too big", message: "The image dimensions are too large." },
    });
  });

  it("doesn't shrink again a photo the browser has already resized", async () => {
    vi.mocked(preparePhoto).mockImplementation(async (file) => ({ blob: file, filename: file.name, converted: true }));
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();

    upload(0).reject(new ApiError({ status: 413, code: "IMAGE_TOO_LARGE", message: "Too large." }));
    await flush();
    expect(pending).toHaveLength(1);
    expect(preparePhoto).toHaveBeenCalledTimes(1);
    expect(queue.getSnapshot()[0]).toMatchObject({ status: "error", error: { title: "Photo too big" } });
  });

  it("cancels the upload and releases the preview when an item is removed", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(3), META);
    await flush();
    const [first] = queue.getSnapshot();

    queue.remove(first?.id ?? "");
    await flush();
    expect(upload(0).options.signal?.aborted).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:photo-1.jpg");
    // The freed slot goes to the next photo.
    expect(statuses(queue)).toEqual(["uploading", "uploading"]);
    expect(pending).toHaveLength(3);

    upload(0).reject(new DOMException("Upload cancelled", "AbortError"));
    await flush();
    expect(statuses(queue)).toEqual(["uploading", "uploading"]);
  });

  it("starts a fresh grid for a new batch once the previous one is done", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(1), META);
    await flush();
    upload(0).resolve(photo("p1"));
    await flush();

    queue.add(files(2), { uploaderName: "", caption: "" });
    expect(queue.getSnapshot()).toHaveLength(2);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
  });

  it("aborts everything on dispose, and stays usable", async () => {
    const queue = new UploadQueue(EVENT_ID, LIMITS);
    queue.add(files(2), META);
    await flush();
    queue.dispose();
    expect(queue.getSnapshot()).toEqual([]);
    expect(pending.every((item) => item.options.signal?.aborted)).toBe(true);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);

    queue.add(files(1), META);
    await flush();
    expect(statuses(queue)).toEqual(["uploading"]);
    expect(pending).toHaveLength(3);
  });
});
