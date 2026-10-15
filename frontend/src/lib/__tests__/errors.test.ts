import { describe, expect, it } from "vitest";

import { ApiError, networkError } from "../api";
import { describeError } from "../errors";

function apiError(code: string, options: { status?: number; message?: string; details?: unknown; retryAfter?: number } = {}) {
  return new ApiError({ status: options.status ?? 400, code, message: options.message ?? `server says ${code}`, ...options });
}

describe("describeError for non-API errors", () => {
  it("uses the message of a plain Error", () => {
    expect(describeError(new Error("Disk full"))).toEqual({ title: "Something went wrong", message: "Disk full", retryable: true });
  });

  it.each([["a string", "boom"], ["undefined", undefined], ["null", null], ["an Error without a message", new Error("")]])(
    "falls back to a generic message for %s",
    (_label, value) => {
      expect(describeError(value)).toEqual({
        title: "Something went wrong",
        message: "Something went wrong.",
        retryable: true,
      });
    },
  );
});

describe("describeError for API errors", () => {
  it("NETWORK_ERROR keeps the specific message", () => {
    expect(describeError(networkError("The upload was interrupted."))).toEqual({
      title: "You're offline",
      message: "The upload was interrupted.",
      retryable: true,
    });
  });

  it("RATE_LIMITED is retryable after Retry-After", () => {
    const friendly = describeError(apiError("RATE_LIMITED", { status: 429, retryAfter: 15 }));
    expect(friendly).toMatchObject({ title: "Slow down a little", retryAfter: 15, retryable: true });
    expect(friendly.message).not.toContain("server says");
  });

  it("SERVICE_BUSY is retryable after Retry-After", () => {
    expect(describeError(apiError("SERVICE_BUSY", { status: 503, retryAfter: 3 }))).toMatchObject({
      title: "The server is busy",
      retryAfter: 3,
      retryable: true,
    });
  });

  it("FILE_TOO_LARGE names the limit when the server gives it", () => {
    expect(describeError(apiError("FILE_TOO_LARGE", { status: 413, details: { max_bytes: 10 * 1024 * 1024 } }))).toEqual({
      title: "Photo too large",
      message: "Please choose a smaller photo (max 10 MB).",
      retryable: false,
    });
  });

  it("GUEST_PHOTO_LIMIT names the per-guest limit when the server gives it", () => {
    expect(describeError(apiError("GUEST_PHOTO_LIMIT", { status: 409, details: { max_photos_per_guest: 15 } }))).toEqual({
      title: "You've shared all your photos",
      message: "Each guest can share up to 15 photos here.",
      retryable: false,
    });
    expect(describeError(apiError("GUEST_PHOTO_LIMIT", { status: 409, details: { max_photos_per_guest: 1 } })).message).toBe(
      "Each guest can share up to 1 photo here.",
    );
  });

  it.each([null, {}, { max_photos_per_guest: "15" }, { max_photos_per_guest: 0 }, { max_photos_per_guest: 2.5 }])(
    "GUEST_PHOTO_LIMIT without a usable limit (%j) has a generic message",
    (details) => {
      expect(describeError(apiError("GUEST_PHOTO_LIMIT", { status: 409, details })).message).toBe(
        "You've reached the photo limit for each guest.",
      );
    },
  );

  it("PAYLOAD_TOO_LARGE without details has no limit", () => {
    expect(describeError(apiError("PAYLOAD_TOO_LARGE", { status: 413 })).message).toBe("Please choose a smaller photo.");
  });

  it.each([
    ["UNSUPPORTED_MEDIA_TYPE", "Unsupported file", false],
    ["INVALID_IMAGE", "Can't read this photo", false],
    ["IMAGE_TOO_LARGE", "Photo too big", false],
    ["UPLOADS_CLOSED", "Uploads are closed", false],
    ["EVENT_PHOTO_LIMIT", "Event is full", false],
    ["GUEST_PHOTO_LIMIT", "You've shared all your photos", false],
    ["EVENT_NOT_FOUND", "Event not found", false],
    ["UNAUTHORIZED", "Not allowed", false],
    ["INSUFFICIENT_STORAGE", "Server storage is full", true],
  ])("%s -> %s", (code, title, retryable) => {
    const friendly = describeError(apiError(code));
    expect(friendly.title).toBe(title);
    expect(friendly.retryable).toBe(retryable);
    expect(friendly.message.length).toBeGreaterThan(0);
  });

  it("passes server messages through where they are specific", () => {
    expect(describeError(apiError("UNSUPPORTED_MEDIA_TYPE", { message: "GIF is not supported." })).message).toBe(
      "GIF is not supported.",
    );
    expect(describeError(apiError("UNAUTHORIZED", { message: "Invalid host token." })).message).toBe("Invalid host token.");
  });

  it("VALIDATION_ERROR shows the first field message without Pydantic's prefix", () => {
    const error = apiError("VALIDATION_ERROR", {
      status: 422,
      message: "Invalid request.",
      details: [{ loc: ["body", "name"], message: "Value error, Name must not be blank" }, { message: "second" }],
    });
    expect(describeError(error)).toEqual({ title: "Check your input", message: "Name must not be blank", retryable: false });
  });

  it("VALIDATION_ERROR falls back to the server message", () => {
    expect(describeError(apiError("VALIDATION_ERROR", { message: "Invalid request.", details: { field: "x" } })).message).toBe(
      "Invalid request.",
    );
    expect(describeError(apiError("VALIDATION_ERROR", { message: "Invalid request.", details: [] })).message).toBe(
      "Invalid request.",
    );
  });

  it("unknown 5xx codes are retryable server problems", () => {
    expect(describeError(apiError("HTTP_502", { status: 502, message: "The server had a problem." }))).toEqual({
      title: "Server problem",
      message: "The server had a problem.",
      retryable: true,
    });
  });

  it("unknown 4xx codes are not retryable", () => {
    expect(describeError(apiError("HTTP_404", { status: 404, message: "Request failed (404)." }))).toEqual({
      title: "Request failed",
      message: "Request failed (404).",
      retryable: false,
    });
  });
});
