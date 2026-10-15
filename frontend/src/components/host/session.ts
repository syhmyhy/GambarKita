import { ApiError } from "../../lib/api";
import { absoluteAppUrl, routes, shareableEventUrl } from "../../lib/env";
import type { EventPublic } from "../../lib/types";

/** Everything a host action needs: which event, the host token, and where failures go. */
export interface HostSession {
  eventId: string;
  token: string;
  /** Show a failed action to the host; a rejected token switches the page to the invalid-link state. */
  reportError: (error: unknown) => void;
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && (error.code === "UNAUTHORIZED" || error.status === 401);
}

export function hasErrorCode(error: unknown, code: string): boolean {
  return error instanceof ApiError && error.code === code;
}

/**
 * The token part of a "#token=..." fragment, or null when the fragment has no
 * token at all. An empty or malformed token is returned as-is so the caller
 * can still strip it from the address bar.
 */
export function readHashToken(hash: string): string | null {
  if (!hash || hash === "#") return null;
  return new URLSearchParams(hash.replace(/^#/, "")).get("token");
}

/** The dashboard page, on the same base as the guest link once the event has loaded. */
function hostDashboardBase(eventId: string, event: EventPublic | null): string {
  const path = routes.host(eventId);
  return event ? shareableEventUrl(event, path) : absoluteAppUrl(path);
}

/** The private dashboard link. The token lives in the fragment, which browsers never send to servers. */
export function hostDashboardUrl(eventId: string, token: string, event: EventPublic | null = null): string {
  return `${hostDashboardBase(eventId, event)}#token=${token}`;
}

/** The dashboard link with all but the start of the token hidden, safe to leave on screen. */
export function maskedHostDashboardUrl(eventId: string, token: string, event: EventPublic | null = null): string {
  return `${hostDashboardBase(eventId, event)}#token=${token.slice(0, 4)}…`;
}

function dismissedKey(eventId: string): string {
  return `gk.host-link-saved.${eventId}`;
}

/** Per-tab memory of "I've saved my host link"; storage may be unavailable, so failures are ignored. */
export const hostLinkReminder = {
  isDismissed(eventId: string): boolean {
    try {
      return window.sessionStorage.getItem(dismissedKey(eventId)) === "1";
    } catch {
      return false;
    }
  },
  dismiss(eventId: string): void {
    try {
      window.sessionStorage.setItem(dismissedKey(eventId), "1");
    } catch {
      // The reminder simply comes back on the next visit.
    }
  },
};
