/**
 * Small, failure-proof wrappers around localStorage. Storage can be unavailable
 * (private mode, disabled site data), so every call is guarded and callers
 * must work without it.
 */

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Ignore: persistence is a convenience, never a requirement.
  }
}

const HOST_TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;

/** Host tokens are kept per event on the host's own device only. */
export const hostTokens = {
  get(eventId: string): string | null {
    const token = read(`gk.host.${eventId}`);
    return token && HOST_TOKEN_PATTERN.test(token) ? token : null;
  },
  set(eventId: string, token: string): void {
    if (HOST_TOKEN_PATTERN.test(token)) write(`gk.host.${eventId}`, token);
  },
  remove(eventId: string): void {
    write(`gk.host.${eventId}`, null);
  },
  isPlausible(token: string): boolean {
    return HOST_TOKEN_PATTERN.test(token);
  },
};

export interface HostedEvent {
  id: string;
  name: string;
  createdAt: string;
}

const HOSTED_KEY = "gk.hosted-events";
const MAX_HOSTED = 20;

/** Events created on this device, so hosts can find their dashboards again. */
export const hostedEvents = {
  list(): HostedEvent[] {
    try {
      const parsed: unknown = JSON.parse(read(HOSTED_KEY) ?? "[]");
      if (!Array.isArray(parsed)) return [];
      return parsed.filter(
        (item): item is HostedEvent =>
          typeof item?.id === "string" && /^[a-z0-9]{12}$/.test(item.id) && typeof item.name === "string" && typeof item.createdAt === "string",
      );
    } catch {
      return [];
    }
  },
  add(event: HostedEvent): void {
    const others = hostedEvents.list().filter((item) => item.id !== event.id);
    write(HOSTED_KEY, JSON.stringify([event, ...others].slice(0, MAX_HOSTED)));
  },
  rename(id: string, name: string): void {
    write(HOSTED_KEY, JSON.stringify(hostedEvents.list().map((item) => (item.id === id ? { ...item, name } : item))));
  },
  remove(id: string): void {
    write(HOSTED_KEY, JSON.stringify(hostedEvents.list().filter((item) => item.id !== id)));
    hostTokens.remove(id);
  },
};

/** Keys made during this page load: used where storage is unavailable, so the key stays the same until reload. */
const sessionGuestKeys = new Map<string, string>();

function newGuestKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * A random key per event that stands for "this guest" when the server limits how
 * many photos each guest can share. It says nothing about the person, and every
 * event gets its own, so events can't be linked. Clearing site data starts afresh.
 */
export const guestKeys = {
  /** Whether this browser has a key for the event, i.e. the guest has been here before. */
  has(eventId: string): boolean {
    if (sessionGuestKeys.has(eventId)) return true;
    const stored = read(`gk.guest.${eventId}`);
    return stored !== null && HOST_TOKEN_PATTERN.test(stored);
  },
  get(eventId: string): string {
    const storageKey = `gk.guest.${eventId}`;
    const stored = read(storageKey);
    if (stored && HOST_TOKEN_PATTERN.test(stored)) return stored;
    let key = sessionGuestKeys.get(eventId);
    if (!key) {
      key = newGuestKey();
      sessionGuestKeys.set(eventId, key);
    }
    write(storageKey, key);
    return key;
  },
};

/** How many columns the live wall grid shows on this device (pinch or zoom buttons); null = automatic. */
export const wallColumns = {
  get: (): number | null => {
    const value = Number(read("gk.wall-columns"));
    return Number.isInteger(value) && value >= 1 && value <= 12 ? value : null;
  },
  set: (columns: number | null): void => write("gk.wall-columns", columns === null ? null : String(columns)),
};

/** The name a guest typed last time, so they don't have to retype it. */
export const guestName = {
  get: (): string => read("gk.guest-name") ?? "",
  set: (name: string): void => write("gk.guest-name", name.trim() ? name.trim() : null),
};
