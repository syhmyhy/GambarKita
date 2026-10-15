import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api";
import { apiUrl } from "../env";
import { guestKeys } from "../storage";
import { uploadPhoto } from "../upload";
import { EVENT_ID, makePhoto } from "./fixtures";

/** Stand-in for XMLHttpRequest that the test drives by hand. */
class FakeXHR {
  static instances: FakeXHR[] = [];

  method = "";
  url = "";
  requestHeaders: Record<string, string> = {};
  withCredentials = true;
  timeout = 0;
  body: unknown = undefined;
  sendCount = 0;
  abortCount = 0;
  status = 0;
  responseText = "";
  responseHeaders: Record<string, string> = {};
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;

  constructor() {
    FakeXHR.instances.push(this);
  }

  open(method: string, url: string): void {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(name: string, value: string): void {
    this.requestHeaders[name] = value;
  }

  getResponseHeader(name: string): string | null {
    return this.responseHeaders[name.toLowerCase()] ?? null;
  }

  send(body: unknown): void {
    this.body = body;
    this.sendCount += 1;
  }

  abort(): void {
    this.abortCount += 1;
    this.onabort?.();
  }

  // Test helpers.
  respond(status: number, body: unknown, headers: Record<string, string> = {}): void {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.responseHeaders = Object.fromEntries(Object.entries(headers).map(([key, value]) => [key.toLowerCase(), value]));
    this.onload?.();
  }

  progress(loaded: number, total: number, lengthComputable = true): void {
    this.upload.onprogress?.({ loaded, total, lengthComputable } as ProgressEvent);
  }

  get form(): FormData {
    if (!(this.body instanceof FormData)) throw new Error("The request body is not FormData");
    return this.body;
  }
}

function currentXhr(): FakeXHR {
  const xhr = FakeXHR.instances.at(-1);
  if (!xhr) throw new Error("No XMLHttpRequest was created");
  return xhr;
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the promise to reject");
}

const jpeg = () => new Blob(["fake-jpeg-bytes"], { type: "image/jpeg" });

beforeEach(() => {
  FakeXHR.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadPhoto request", () => {
  it("identifies the guest with this browser's key for the event, the same on every upload", () => {
    void uploadPhoto(EVENT_ID, jpeg(), "one.jpg");
    void uploadPhoto(EVENT_ID, jpeg(), "two.jpg");
    void uploadPhoto("zzzzzzzzzzzz", jpeg(), "elsewhere.jpg");
    const [first, second, other] = FakeXHR.instances.map((xhr) => xhr.requestHeaders["X-Guest-Key"]);

    expect(first).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(second).toBe(first);
    expect(other).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(other).not.toBe(first);
  });

  it("POSTs multipart form data to the event's photos endpoint", () => {
    void uploadPhoto(EVENT_ID, jpeg(), "IMG_0001.jpg", { uploaderName: "  Aisyah  ", caption: "  First dance! " });
    const xhr = currentXhr();

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe(apiUrl(`/api/events/${EVENT_ID}/photos`));
    expect(xhr.requestHeaders).toEqual({ Accept: "application/json", "X-Guest-Key": guestKeys.get(EVENT_ID) });
    expect(xhr.withCredentials).toBe(false);
    expect(xhr.timeout).toBe(5 * 60 * 1000);
    expect(xhr.sendCount).toBe(1);

    const file = xhr.form.get("file");
    expect(file).toBeInstanceOf(File);
    expect((file as File).name).toBe("IMG_0001.jpg");
    expect((file as File).type).toBe("image/jpeg");
    expect((file as File).size).toBe("fake-jpeg-bytes".length);
    expect(xhr.form.get("uploader_name")).toBe("Aisyah");
    expect(xhr.form.get("caption")).toBe("First dance!");
  });

  it("URL-encodes the event id", () => {
    void uploadPhoto("a/b", jpeg(), "x.jpg");
    expect(currentXhr().url).toBe(apiUrl("/api/events/a%2Fb/photos"));
  });

  it.each([
    ["missing", {}],
    ["blank", { uploaderName: "   ", caption: "\n\t " }],
    ["empty", { uploaderName: "", caption: "" }],
  ])("omits %s name and caption fields", (_label, options) => {
    void uploadPhoto(EVENT_ID, jpeg(), "x.jpg", options);
    const form = currentXhr().form;
    expect(form.has("uploader_name")).toBe(false);
    expect(form.has("caption")).toBe(false);
    expect(form.has("file")).toBe(true);
  });
});

describe("uploadPhoto progress", () => {
  it("reports the sent fraction", () => {
    const onProgress = vi.fn<(fraction: number) => void>();
    void uploadPhoto(EVENT_ID, jpeg(), "x.jpg", { onProgress });
    const xhr = currentXhr();
    xhr.progress(0, 400);
    xhr.progress(100, 400);
    xhr.progress(400, 400);
    expect(onProgress.mock.calls.map(([fraction]) => fraction)).toEqual([0, 0.25, 1]);
  });

  it("ignores progress events without a known total", () => {
    const onProgress = vi.fn<(fraction: number) => void>();
    void uploadPhoto(EVENT_ID, jpeg(), "x.jpg", { onProgress });
    const xhr = currentXhr();
    xhr.progress(100, 400, false);
    xhr.progress(0, 0, true);
    expect(onProgress).not.toHaveBeenCalled();
  });

  it("works without a progress callback", () => {
    void uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    expect(() => currentXhr().progress(1, 2)).not.toThrow();
  });
});

describe("uploadPhoto responses", () => {
  it("resolves with the created photo on 201", async () => {
    const photo = makePhoto("p1", 1, { uploader_name: "Aisyah" });
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().respond(201, photo);
    await expect(promise).resolves.toEqual(photo);
  });

  it("rejects a 201 with an unreadable body", async () => {
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().respond(201, "<html>oops</html>");
    const error = await rejection(promise);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", message: "The server sent an unexpected response." });
  });

  it("rejects with the ApiError from the error envelope", async () => {
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().respond(413, {
      error: { code: "FILE_TOO_LARGE", message: "Too large", details: { max_bytes: 10_485_760 }, request_id: "r9" },
    });
    const error = await rejection(promise);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 413,
      code: "FILE_TOO_LARGE",
      message: "Too large",
      details: { max_bytes: 10_485_760 },
      requestId: "r9",
    });
  });

  it("passes Retry-After through", async () => {
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().respond(429, { error: { code: "RATE_LIMITED", message: "Slow down" } }, { "Retry-After": "12" });
    await expect(promise).rejects.toMatchObject({ code: "RATE_LIMITED", retryAfter: 12 });
  });

  it("maps a proxy's non-JSON 413 to PAYLOAD_TOO_LARGE", async () => {
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().respond(413, "<html>413 Request Entity Too Large</html>");
    await expect(promise).rejects.toMatchObject({ status: 413, code: "PAYLOAD_TOO_LARGE" });
  });

  it("rejects with NETWORK_ERROR when the connection drops", async () => {
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().onerror?.();
    const error = await rejection(promise);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "NETWORK_ERROR" });
    expect((error as ApiError).message).toMatch(/interrupted/);
  });

  it("rejects with NETWORK_ERROR on timeout", async () => {
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg");
    currentXhr().ontimeout?.();
    await expect(promise).rejects.toMatchObject({ code: "NETWORK_ERROR", message: expect.stringMatching(/too long/) });
  });
});

describe("uploadPhoto cancellation", () => {
  it("aborts the request when the signal fires", async () => {
    const controller = new AbortController();
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg", { signal: controller.signal });
    const xhr = currentXhr();
    controller.abort();
    expect(xhr.abortCount).toBe(1);
    const error = await rejection(promise);
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe("AbortError");
  });

  it("rejects at once without a request for an already-aborted signal", async () => {
    const controller = new AbortController();
    controller.abort();
    const error = await rejection(uploadPhoto(EVENT_ID, jpeg(), "x.jpg", { signal: controller.signal }));
    expect(error).toBeInstanceOf(DOMException);
    expect((error as DOMException).name).toBe("AbortError");
    expect(FakeXHR.instances).toHaveLength(0);
  });

  it("does not abort a finished upload when the signal fires later", async () => {
    const controller = new AbortController();
    const photo = makePhoto("p1", 1);
    const promise = uploadPhoto(EVENT_ID, jpeg(), "x.jpg", { signal: controller.signal });
    currentXhr().respond(201, photo);
    await expect(promise).resolves.toEqual(photo);
    controller.abort(); // a real XHR ignores abort() once done; the promise is already settled
    await expect(promise).resolves.toEqual(photo);
  });
});
