import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, errorFromResponse, networkError } from "../api";
import { apiUrl } from "../env";
import { guestKeys } from "../storage";
import { EVENT_ID, makeEvent } from "./fixtures";

const TOKEN = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFy";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers as Record<string, string> | undefined) },
  });
}

function envelope(code: string, message: string, extra: Record<string, unknown> = {}) {
  return { error: { code, message, details: null, request_id: "req-123", ...extra } };
}

function lastRequest() {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was not called");
  const [url, init = {}] = call;
  return { url: String(url), init, headers: (init.headers ?? {}) as Record<string, string> };
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the promise to reject");
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiError", () => {
  it("is an Error carrying the envelope fields", () => {
    const error = new ApiError({ status: 429, code: "RATE_LIMITED", message: "Slow down", retryAfter: 5, requestId: "r1" });
    expect(error).toBeInstanceOf(Error);
    expect(error.name).toBe("ApiError");
    expect(error.message).toBe("Slow down");
    expect(error.status).toBe(429);
    expect(error.code).toBe("RATE_LIMITED");
    expect(error.retryAfter).toBe(5);
    expect(error.requestId).toBe("r1");
    expect(error.isNetworkError).toBe(false);
  });

  it("networkError() builds a status-0 NETWORK_ERROR", () => {
    const error = networkError();
    expect(error.status).toBe(0);
    expect(error.code).toBe("NETWORK_ERROR");
    expect(error.isNetworkError).toBe(true);
    expect(error.message).toMatch(/can't reach the server/i);
    expect(networkError("Custom").message).toBe("Custom");
  });
});

describe("errorFromResponse", () => {
  it("parses the error envelope", () => {
    const body = JSON.stringify(envelope("UPLOADS_CLOSED", "Uploads are closed.", { details: { hint: "x" } }));
    const error = errorFromResponse(403, body, null);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(403);
    expect(error.code).toBe("UPLOADS_CLOSED");
    expect(error.message).toBe("Uploads are closed.");
    expect(error.details).toEqual({ hint: "x" });
    expect(error.requestId).toBe("req-123");
    expect(error.retryAfter).toBeUndefined();
  });

  it("reads Retry-After seconds", () => {
    const body = JSON.stringify(envelope("RATE_LIMITED", "Too many requests"));
    expect(errorFromResponse(429, body, "30").retryAfter).toBe(30);
    expect(errorFromResponse(429, body, "0").retryAfter).toBe(0);
    expect(errorFromResponse(429, body, "1.5").retryAfter).toBe(1.5);
  });

  it.each([null, "", "-5", "soon", "Wed, 21 Oct 2026 07:28:00 GMT"])("ignores an unusable Retry-After (%j)", (header) => {
    expect(errorFromResponse(429, JSON.stringify(envelope("RATE_LIMITED", "x")), header).retryAfter).toBeUndefined();
  });

  it("maps a non-JSON 413 (proxy body limit) to PAYLOAD_TOO_LARGE", () => {
    const error = errorFromResponse(413, "<html><body>413 Request Entity Too Large</body></html>", null);
    expect(error.code).toBe("PAYLOAD_TOO_LARGE");
    expect(error.status).toBe(413);
    expect(error.message).toBe("Request failed (413).");
  });

  it.each([500, 502, 503, 504])("gives a friendly message for a non-JSON %i", (status) => {
    const error = errorFromResponse(status, "Bad Gateway", "12");
    expect(error.code).toBe(`HTTP_${status}`);
    expect(error.message).toBe("The server had a problem. Please try again.");
    expect(error.retryAfter).toBe(12);
    expect(error.message).not.toContain("Bad Gateway");
  });

  it("falls back for other non-JSON client errors", () => {
    const error = errorFromResponse(404, "Not Found", null);
    expect(error.code).toBe("HTTP_404");
    expect(error.message).toBe("Request failed (404).");
  });

  it.each([
    ["empty body", ""],
    ["JSON null", "null"],
    ["a JSON array", "[1, 2]"],
    ["a JSON string", '"oops"'],
    ["an envelope without a message", JSON.stringify({ error: { code: "X" } })],
    ["an envelope with a non-string code", JSON.stringify({ error: { code: 42, message: "m" } })],
    ["a FastAPI detail body", JSON.stringify({ detail: "Not Found" })],
  ])("falls back for %s", (_label, body) => {
    const error = errorFromResponse(400, body, null);
    expect(error.code).toBe("HTTP_400");
    expect(error.message).toBe("Request failed (400).");
  });
});

describe("api requests", () => {
  it("GETs JSON without credentials, referrer or auth", async () => {
    const config = { max_upload_bytes: 1 };
    fetchMock.mockResolvedValue(jsonResponse(config));
    await expect(api.getConfig()).resolves.toEqual(config);

    const { url, init, headers } = lastRequest();
    expect(url).toBe(apiUrl("/api/config"));
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(init.credentials).toBe("omit");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(headers).toEqual({ Accept: "application/json" });
  });

  it("passes the abort signal and URL-encodes the event id", async () => {
    fetchMock.mockResolvedValue(jsonResponse(makeEvent()));
    const controller = new AbortController();
    await api.getEvent("a/b?c", controller.signal);
    const { url, init } = lastRequest();
    expect(url).toBe(apiUrl("/api/events/a%2Fb%3Fc"));
    expect(init.signal).toBe(controller.signal);
  });

  it("sends the host token as a Bearer header and the body as JSON", async () => {
    const updated = makeEvent({ name: "Renamed", uploads_open: false });
    fetchMock.mockResolvedValue(jsonResponse(updated));
    await expect(api.updateEvent(EVENT_ID, TOKEN, { name: "Renamed", uploads_open: false })).resolves.toEqual(updated);

    const { url, init, headers } = lastRequest();
    expect(url).toBe(apiUrl(`/api/events/${EVENT_ID}`));
    expect(url).not.toContain(TOKEN);
    expect(init.method).toBe("PATCH");
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({ name: "Renamed", uploads_open: false });
  });

  it("sends the event creation key only when one is given", async () => {
    fetchMock.mockImplementation(async () => jsonResponse({ event: makeEvent(), host_token: TOKEN, host_url: "x" }, { status: 201 }));

    await api.createEvent("Party", "s3cret");
    let request = lastRequest();
    expect(request.url).toBe(apiUrl("/api/events"));
    expect(request.init.method).toBe("POST");
    expect(request.headers["X-Event-Creation-Key"]).toBe("s3cret");
    expect(request.headers.Authorization).toBeUndefined();
    expect(JSON.parse(String(request.init.body))).toEqual({ name: "Party" });

    await api.createEvent("Party");
    request = lastRequest();
    expect(request.headers).not.toHaveProperty("X-Event-Creation-Key");
  });

  it("resolves 204 responses to undefined", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.deleteEvent(EVENT_ID, TOKEN)).resolves.toBeUndefined();
    const { url, init, headers } = lastRequest();
    expect(url).toBe(apiUrl(`/api/events/${EVENT_ID}`));
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
    expect(headers["Content-Type"]).toBeUndefined();
  });

  it("verifies a host token", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(api.verifyHost(EVENT_ID, TOKEN)).resolves.toBeUndefined();
    const { url, init, headers } = lastRequest();
    expect(url).toBe(apiUrl(`/api/events/${EVENT_ID}/host/verify`));
    expect(init.method).toBe("POST");
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("rejects a wrong host token with UNAUTHORIZED", async () => {
    fetchMock.mockResolvedValue(jsonResponse(envelope("UNAUTHORIZED", "Invalid host token."), { status: 401 }));
    const error = await rejection(api.verifyHost(EVENT_ID, "wrong"));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 401, code: "UNAUTHORIZED", message: "Invalid host token." });
  });

  it("deletes a photo", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await api.deletePhoto(EVENT_ID, "photo/1", TOKEN);
    const { url, init, headers } = lastRequest();
    expect(url).toBe(apiUrl(`/api/events/${EVENT_ID}/photos/photo%2F1`));
    expect(init.method).toBe("DELETE");
    expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("asks for this guest's quota with the browser's guest key for the event", async () => {
    const quota = { limit: 15, used: 3, remaining: 12 };
    fetchMock.mockResolvedValue(jsonResponse(quota));
    await expect(api.getGuestQuota(EVENT_ID)).resolves.toEqual(quota);
    const { url, init, headers } = lastRequest();
    expect(url).toBe(apiUrl(`/api/events/${EVENT_ID}/guest-quota`));
    expect(init.method).toBe("GET");
    expect(headers["X-Guest-Key"]).toBe(guestKeys.get(EVENT_ID));
    expect(headers.Authorization).toBeUndefined();
  });

  it("lists photos with a default limit and no cursor", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    await expect(api.listPhotos(EVENT_ID)).resolves.toEqual({ items: [], next_cursor: null });
    const { url, headers } = lastRequest();
    expect(url).toBe(apiUrl(`/api/events/${EVENT_ID}/photos?limit=50`));
    expect(headers.Authorization).toBeUndefined();
  });

  it("lists photos with a limit and an encoded cursor", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    const controller = new AbortController();
    await api.listPhotos(EVENT_ID, { cursor: "abc+/==", limit: 48, signal: controller.signal });
    const { url, init } = lastRequest();
    const parsed = new URL(url, "http://test.invalid");
    expect(parsed.pathname).toBe(`/api/events/${EVENT_ID}/photos`);
    expect(parsed.searchParams.get("limit")).toBe("48");
    expect(parsed.searchParams.get("cursor")).toBe("abc+/==");
    expect(init.signal).toBe(controller.signal);
  });

  it("omits a null cursor", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    await api.listPhotos(EVENT_ID, { cursor: null });
    expect(lastRequest().url).not.toContain("cursor");
  });
});

describe("api errors", () => {
  it("turns an error envelope into an ApiError with Retry-After", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(envelope("RATE_LIMITED", "Too many requests."), { status: 429, headers: { "Retry-After": "7" } }),
    );
    const error = await rejection(api.getEvent(EVENT_ID));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "RATE_LIMITED", retryAfter: 7, requestId: "req-123" });
  });

  it("turns a non-JSON server error into a friendly ApiError", async () => {
    fetchMock.mockResolvedValue(new Response("<h1>502 Bad Gateway</h1>", { status: 502 }));
    const error = await rejection(api.getEvent(EVENT_ID));
    expect(error).toMatchObject({ status: 502, code: "HTTP_502", message: "The server had a problem. Please try again." });
  });

  it("turns a failed fetch into NETWORK_ERROR (status 0)", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await rejection(api.getEvent(EVENT_ID));
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, code: "NETWORK_ERROR", isNetworkError: true });
  });

  it("lets an AbortError through unchanged", async () => {
    const abort = new DOMException("The operation was aborted.", "AbortError");
    fetchMock.mockRejectedValue(abort);
    await expect(api.getEvent(EVENT_ID, new AbortController().signal)).rejects.toBe(abort);
  });

  it("treats other DOMExceptions as network errors", async () => {
    fetchMock.mockRejectedValue(new DOMException("Network failure", "NetworkError"));
    const error = await rejection(api.listPhotos(EVENT_ID));
    expect(error).toMatchObject({ code: "NETWORK_ERROR" });
  });
});
