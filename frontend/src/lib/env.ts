/**
 * Where the API lives. Empty (the default) means "same origin": the Vite dev
 * server proxies /api, /media and /ws, and production should do the same.
 */
export const API_BASE: string = (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");

/** Absolute or origin-relative URL for an API path such as "/api/events". */
export function apiUrl(path: string): string {
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * URL for a media path returned by the API. Only server-generated
 * "/media/..." paths are accepted, as defence in depth against a tampered
 * payload pointing images elsewhere.
 */
export function mediaUrl(path: string): string {
  if (!/^\/media\/events\/[a-z0-9]{12}\/(photos|thumbs)\/[a-f0-9]{32}\.(jpg|png|webp)$/.test(path)) {
    return "";
  }
  return apiUrl(path);
}

/** WebSocket URL for an API path, derived from API_BASE or the page's own origin. */
export function wsUrl(path: string, location: Pick<Location, "protocol" | "host"> = window.location): string {
  if (API_BASE) {
    return `${API_BASE.replace(/^http/i, "ws")}${path}`;
  }
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}${path}`;
}

/** Frontend routes. They match the links the API builds from GK_PUBLIC_APP_URL. */
export const routes = {
  home: () => "/",
  upload: (eventId: string) => `/e/${eventId}`,
  wall: (eventId: string) => `/e/${eventId}/wall`,
  host: (eventId: string) => `/e/${eventId}/host`,
};

/** Absolute URL of a frontend route on this origin (used for QR codes and sharing). */
export function absoluteAppUrl(path: string): string {
  return new URL(path, window.location.origin).toString();
}

export const EVENT_ID_PATTERN = /^[a-z0-9]{12}$/;

const LOOPBACK_HOST = /^(localhost|127(?:\.\d{1,3}){3}|\[::1\])$/i;

/**
 * The link guests should open (and the QR code should encode) for an event.
 * Normally this page's own origin. But when this page runs on a loopback
 * address (e.g. the host opened http://localhost:5173), a QR code pointing at
 * "localhost" would not work on guests' phones, so the server's configured
 * public link (GK_PUBLIC_APP_URL) is preferred when it is usable.
 */
export function guestUploadUrl(
  event: { id: string; links: { upload: string } },
  location: Pick<Location, "hostname" | "origin"> = window.location,
): string {
  const own = new URL(routes.upload(event.id), location.origin).toString();
  if (!LOOPBACK_HOST.test(location.hostname)) return own;
  try {
    const server = new URL(event.links.upload);
    const usable =
      (server.protocol === "http:" || server.protocol === "https:") &&
      !LOOPBACK_HOST.test(server.hostname) &&
      server.pathname === routes.upload(event.id);
    return usable ? server.toString() : own;
  } catch {
    return own;
  }
}

/** Absolute URL of another route of the same event, on the same base as ``guestUploadUrl``. */
export function shareableEventUrl(
  event: { id: string; links: { upload: string } },
  path: string,
  location: Pick<Location, "hostname" | "origin"> = window.location,
): string {
  return new URL(path, new URL(guestUploadUrl(event, location)).origin).toString();
}
