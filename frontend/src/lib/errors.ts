import { ApiError } from "./api";
import { formatBytes, pluralize } from "./format";

export interface FriendlyError {
  title: string;
  message: string;
  /** Seconds to wait before retrying makes sense, if the server said so. */
  retryAfter?: number;
  /** Retrying the same action could succeed (network blips, rate limits, busy server). */
  retryable: boolean;
}

/** Turn any thrown value into a short, human message for toasts and error states. */
export function describeError(error: unknown): FriendlyError {
  if (!(error instanceof ApiError)) {
    const message = error instanceof Error && error.message ? error.message : "Something went wrong.";
    return { title: "Something went wrong", message, retryable: true };
  }

  const details = (error.details ?? {}) as Record<string, unknown>;
  switch (error.code) {
    case "NETWORK_ERROR":
      return { title: "You're offline", message: error.message, retryable: true };
    case "RATE_LIMITED":
      return {
        title: "Slow down a little",
        message: "Lots of activity right now. Please try again in a moment.",
        retryAfter: error.retryAfter,
        retryable: true,
      };
    case "SERVICE_BUSY":
      return {
        title: "The server is busy",
        message: "Many photos are being processed. Please try again shortly.",
        retryAfter: error.retryAfter,
        retryable: true,
      };
    case "FILE_TOO_LARGE":
    case "PAYLOAD_TOO_LARGE": {
      const max = typeof details.max_bytes === "number" ? ` (max ${formatBytes(details.max_bytes)})` : "";
      return { title: "Photo too large", message: `Please choose a smaller photo${max}.`, retryable: false };
    }
    case "UNSUPPORTED_MEDIA_TYPE":
      return { title: "Unsupported file", message: error.message, retryable: false };
    case "INVALID_IMAGE":
      return { title: "Can't read this photo", message: "The file seems damaged or isn't a photo.", retryable: false };
    case "IMAGE_TOO_LARGE":
      return { title: "Photo too big", message: error.message, retryable: false };
    case "UPLOADS_CLOSED":
      return { title: "Uploads are closed", message: "The host has stopped accepting new photos.", retryable: false };
    case "EVENT_PHOTO_LIMIT":
      return { title: "Event is full", message: "This event has reached its photo limit.", retryable: false };
    case "GUEST_PHOTO_LIMIT": {
      const limit = guestLimitOf(error);
      return {
        title: "You've shared all your photos",
        message:
          limit === null
            ? "You've reached the photo limit for each guest."
            : `Each guest can share up to ${pluralize(limit, "photo")} here.`,
        retryable: false,
      };
    }
    case "EVENT_NOT_FOUND":
      return { title: "Event not found", message: "Check the link or QR code and try again.", retryable: false };
    case "UNAUTHORIZED":
      return { title: "Not allowed", message: error.message, retryable: false };
    case "INSUFFICIENT_STORAGE":
      return { title: "Server storage is full", message: error.message, retryable: true };
    case "VALIDATION_ERROR":
      return { title: "Check your input", message: validationMessage(error) ?? error.message, retryable: false };
    default:
      return {
        title: error.status >= 500 ? "Server problem" : "Request failed",
        message: error.message,
        retryable: error.status >= 500,
      };
  }
}

/** The per-guest limit a GUEST_PHOTO_LIMIT error reports, if any. */
export function guestLimitOf(error: ApiError): number | null {
  const limit = (error.details as { max_photos_per_guest?: unknown } | null | undefined)?.max_photos_per_guest;
  return typeof limit === "number" && Number.isInteger(limit) && limit > 0 ? limit : null;
}

function validationMessage(error: ApiError): string | undefined {
  if (!Array.isArray(error.details)) return undefined;
  const first = error.details[0] as { message?: unknown } | undefined;
  return typeof first?.message === "string" ? first.message.replace(/^Value error, /, "") : undefined;
}
