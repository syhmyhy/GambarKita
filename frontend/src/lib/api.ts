import { apiUrl } from "./env";
import { guestKeys } from "./storage";
import type { ErrorEnvelope, EventCreated, EventPublic, GuestQuota, PhotoPage, PublicConfig } from "./types";

/** Error with the backend's structured envelope ({"error": {code, message, ...}}). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  readonly requestId: string | undefined;
  readonly retryAfter: number | undefined;

  constructor(options: {
    status: number;
    code: string;
    message: string;
    details?: unknown;
    requestId?: string;
    retryAfter?: number;
  }) {
    super(options.message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.details = options.details;
    this.requestId = options.requestId;
    this.retryAfter = options.retryAfter;
  }

  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

export function networkError(message = "Can't reach the server. Check your connection and try again."): ApiError {
  return new ApiError({ status: 0, code: "NETWORK_ERROR", message });
}

function parseRetryAfter(value: string | null): number | undefined {
  if (!value) return undefined;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined;
}

/** Build an ApiError from a status code, response text and Retry-After header. */
export function errorFromResponse(status: number, bodyText: string, retryAfterHeader: string | null): ApiError {
  const retryAfter = parseRetryAfter(retryAfterHeader);
  try {
    const envelope = JSON.parse(bodyText) as Partial<ErrorEnvelope>;
    const error = envelope?.error;
    if (error && typeof error.code === "string" && typeof error.message === "string") {
      return new ApiError({
        status,
        code: error.code,
        message: error.message,
        details: error.details,
        requestId: error.request_id,
        retryAfter,
      });
    }
  } catch {
    // Not JSON (e.g. a proxy error page): fall through to a generic error.
  }
  return new ApiError({
    status,
    code: status === 413 ? "PAYLOAD_TOO_LARGE" : `HTTP_${status}`,
    message: status >= 500 ? "The server had a problem. Please try again." : `Request failed (${status}).`,
    retryAfter,
  });
}

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  hostToken?: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.hostToken) headers.Authorization = `Bearer ${options.hostToken}`;

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method: options.method ?? "GET",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw networkError();
  }

  if (!response.ok) {
    throw errorFromResponse(response.status, await response.text(), response.headers.get("Retry-After"));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  getConfig: (signal?: AbortSignal) => request<PublicConfig>("/api/config", { signal }),

  createEvent: (name: string, creationKey?: string) =>
    request<EventCreated>("/api/events", {
      method: "POST",
      body: { name },
      headers: creationKey ? { "X-Event-Creation-Key": creationKey } : undefined,
    }),

  getEvent: (eventId: string, signal?: AbortSignal) =>
    request<EventPublic>(`/api/events/${encodeURIComponent(eventId)}`, { signal }),

  /** How many photos this guest (this browser) has shared to the event, and may still share. */
  getGuestQuota: (eventId: string, signal?: AbortSignal) =>
    request<GuestQuota>(`/api/events/${encodeURIComponent(eventId)}/guest-quota`, {
      headers: { "X-Guest-Key": guestKeys.get(eventId) },
      signal,
    }),

  updateEvent: (eventId: string, hostToken: string, changes: { name?: string; uploads_open?: boolean }) =>
    request<EventPublic>(`/api/events/${encodeURIComponent(eventId)}`, { method: "PATCH", body: changes, hostToken }),

  deleteEvent: (eventId: string, hostToken: string) =>
    request<void>(`/api/events/${encodeURIComponent(eventId)}`, { method: "DELETE", hostToken }),

  verifyHost: (eventId: string, hostToken: string) =>
    request<void>(`/api/events/${encodeURIComponent(eventId)}/host/verify`, { method: "POST", hostToken }),

  listPhotos: (eventId: string, options: { cursor?: string | null; limit?: number; signal?: AbortSignal } = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(options.limit ?? 50));
    if (options.cursor) params.set("cursor", options.cursor);
    return request<PhotoPage>(`/api/events/${encodeURIComponent(eventId)}/photos?${params}`, {
      signal: options.signal,
    });
  },

  deletePhoto: (eventId: string, photoId: string, hostToken: string) =>
    request<void>(`/api/events/${encodeURIComponent(eventId)}/photos/${encodeURIComponent(photoId)}`, {
      method: "DELETE",
      hostToken,
    }),
};
