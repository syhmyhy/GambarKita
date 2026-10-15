import { describe, expect, it, vi } from "vitest";

import { type HostedEvent, guestKeys, guestName, hostTokens, hostedEvents } from "../storage";

const TOKEN = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFy_-12";
const HOSTED_KEY = "gk.hosted-events";

function eventId(index: number): string {
  return `event${String(index).padStart(7, "0")}`;
}

function hosted(index: number, name = `Event ${index}`): HostedEvent {
  return { id: eventId(index), name, createdAt: new Date(Date.UTC(2026, 0, 1, 0, index)).toISOString() };
}

describe("hostTokens", () => {
  it("stores tokens per event", () => {
    hostTokens.set("abcdefghijkl", TOKEN);
    expect(hostTokens.get("abcdefghijkl")).toBe(TOKEN);
    expect(hostTokens.get("zzzzzzzzzzzz")).toBeNull();
    expect(window.localStorage.getItem("gk.host.abcdefghijkl")).toBe(TOKEN);
  });

  it("does not store an implausible token", () => {
    hostTokens.set("abcdefghijkl", "short");
    hostTokens.set("abcdefghijkl", `${TOKEN}<script>`);
    expect(window.localStorage.getItem("gk.host.abcdefghijkl")).toBeNull();
    expect(hostTokens.get("abcdefghijkl")).toBeNull();
  });

  it("ignores a tampered stored token", () => {
    window.localStorage.setItem("gk.host.abcdefghijkl", "not a token");
    expect(hostTokens.get("abcdefghijkl")).toBeNull();
  });

  it("removes a token", () => {
    hostTokens.set("abcdefghijkl", TOKEN);
    hostTokens.remove("abcdefghijkl");
    expect(hostTokens.get("abcdefghijkl")).toBeNull();
  });

  it.each([
    ["32 url-safe characters", "a".repeat(32), true],
    ["128 characters", "A".repeat(128), true],
    ["dashes and underscores", `${"a".repeat(30)}-_`, true],
    ["31 characters", "a".repeat(31), false],
    ["129 characters", "a".repeat(129), false],
    ["standard base64 symbols", `${"a".repeat(32)}+/=`, false],
    ["whitespace", ` ${"a".repeat(32)}`, false],
    ["an empty string", "", false],
  ])("isPlausible: %s", (_label, token, expected) => {
    expect(hostTokens.isPlausible(token)).toBe(expected);
  });
});

describe("hostedEvents", () => {
  it("starts empty", () => {
    expect(hostedEvents.list()).toEqual([]);
  });

  it("lists the newest event first", () => {
    hostedEvents.add(hosted(1));
    hostedEvents.add(hosted(2));
    expect(hostedEvents.list().map((item) => item.id)).toEqual([eventId(2), eventId(1)]);
    expect(hostedEvents.list()[0]).toEqual(hosted(2));
  });

  it("moves a re-added event to the front without duplicating it", () => {
    hostedEvents.add(hosted(1));
    hostedEvents.add(hosted(2));
    hostedEvents.add(hosted(1, "Renamed"));
    const list = hostedEvents.list();
    expect(list.map((item) => item.id)).toEqual([eventId(1), eventId(2)]);
    expect(list[0]?.name).toBe("Renamed");
  });

  it("keeps at most 20 events, dropping the oldest", () => {
    for (let index = 0; index < 25; index += 1) hostedEvents.add(hosted(index));
    const ids = hostedEvents.list().map((item) => item.id);
    expect(ids).toHaveLength(20);
    expect(ids[0]).toBe(eventId(24));
    expect(ids.at(-1)).toBe(eventId(5));
    expect(ids).not.toContain(eventId(4));
  });

  it("renames one event", () => {
    hostedEvents.add(hosted(1));
    hostedEvents.add(hosted(2));
    hostedEvents.rename(eventId(1), "Wedding");
    expect(hostedEvents.list()).toEqual([hosted(2), hosted(1, "Wedding")]);
  });

  it("ignores renaming an unknown event", () => {
    hostedEvents.add(hosted(1));
    hostedEvents.rename(eventId(9), "Nope");
    expect(hostedEvents.list()).toEqual([hosted(1)]);
  });

  it("removes an event together with its host token", () => {
    hostedEvents.add(hosted(1));
    hostedEvents.add(hosted(2));
    hostTokens.set(eventId(1), TOKEN);
    hostTokens.set(eventId(2), TOKEN);

    hostedEvents.remove(eventId(1));
    expect(hostedEvents.list()).toEqual([hosted(2)]);
    expect(hostTokens.get(eventId(1))).toBeNull();
    expect(hostTokens.get(eventId(2))).toBe(TOKEN);
  });

  it("survives corrupted JSON", () => {
    window.localStorage.setItem(HOSTED_KEY, "{not json");
    expect(hostedEvents.list()).toEqual([]);
    hostedEvents.add(hosted(1));
    expect(hostedEvents.list()).toEqual([hosted(1)]);
  });

  it.each([
    ["an object", '{"id":"abcdefghijkl"}'],
    ["a number", "42"],
    ["null", "null"],
  ])("treats %s as an empty list", (_label, raw) => {
    window.localStorage.setItem(HOSTED_KEY, raw);
    expect(hostedEvents.list()).toEqual([]);
  });

  it("drops malformed entries", () => {
    const good = hosted(1);
    window.localStorage.setItem(
      HOSTED_KEY,
      JSON.stringify([
        null,
        7,
        "abcdefghijkl",
        { id: "abcdefghijkl" },
        { id: "BAD-ID", name: "x", createdAt: "y" },
        { id: "abcdefghijkm", name: 3, createdAt: "y" },
        { id: "abcdefghijkn", name: "x", createdAt: null },
        good,
      ]),
    );
    expect(hostedEvents.list()).toEqual([good]);
  });
});

describe("guestName", () => {
  it("remembers a trimmed name", () => {
    expect(guestName.get()).toBe("");
    guestName.set("  Aisyah  ");
    expect(guestName.get()).toBe("Aisyah");
  });

  it("forgets the name when set to blank", () => {
    guestName.set("Aisyah");
    guestName.set("   ");
    expect(guestName.get()).toBe("");
    expect(window.localStorage.getItem("gk.guest-name")).toBeNull();
  });
});

describe("guestKeys", () => {
  const KEY_PATTERN = /^[A-Za-z0-9_-]{43}$/;

  it("makes a random key per event and keeps it", () => {
    const key = guestKeys.get(eventId(101));
    expect(key).toMatch(KEY_PATTERN);
    expect(window.localStorage.getItem(`gk.guest.${eventId(101)}`)).toBe(key);
    expect(guestKeys.get(eventId(101))).toBe(key);
    expect(guestKeys.get(eventId(102))).not.toBe(key);
  });

  it("uses a key saved earlier, e.g. before a reload", () => {
    window.localStorage.setItem(`gk.guest.${eventId(103)}`, TOKEN);
    expect(guestKeys.get(eventId(103))).toBe(TOKEN);
  });

  it("replaces a tampered saved key", () => {
    window.localStorage.setItem(`gk.guest.${eventId(104)}`, "not a key");
    const key = guestKeys.get(eventId(104));
    expect(key).toMatch(KEY_PATTERN);
    expect(window.localStorage.getItem(`gk.guest.${eventId(104)}`)).toBe(key);
  });

  it("knows whether the guest has a key for the event", () => {
    expect(guestKeys.has(eventId(106))).toBe(false);
    guestKeys.get(eventId(106));
    expect(guestKeys.has(eventId(106))).toBe(true);

    window.localStorage.setItem(`gk.guest.${eventId(107)}`, TOKEN);
    expect(guestKeys.has(eventId(107))).toBe(true);
    window.localStorage.setItem(`gk.guest.${eventId(108)}`, "not a key");
    expect(guestKeys.has(eventId(108))).toBe(false);
  });

  it("remembers this page load's key when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    });
    expect(guestKeys.has(eventId(109))).toBe(false);
    guestKeys.get(eventId(109));
    expect(guestKeys.has(eventId(109))).toBe(true);
  });

  it("keeps the same key for the page's lifetime when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    });
    const key = guestKeys.get(eventId(105));
    expect(key).toMatch(KEY_PATTERN);
    expect(guestKeys.get(eventId(105))).toBe(key);
  });

  it("is made from fresh randomness each time", () => {
    const keys = new Set(Array.from({ length: 50 }, (_, index) => guestKeys.get(eventId(200 + index))));
    expect(keys.size).toBe(50);
  });
});

describe("when localStorage throws", () => {
  function breakStorage() {
    const failure = () => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(failure);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(failure);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(failure);
  }

  it("reads fall back to empty values", () => {
    hostTokens.set("abcdefghijkl", TOKEN);
    guestName.set("Aisyah");
    hostedEvents.add(hosted(1));
    breakStorage();

    expect(hostTokens.get("abcdefghijkl")).toBeNull();
    expect(guestName.get()).toBe("");
    expect(hostedEvents.list()).toEqual([]);
  });

  it("writes are silently ignored", () => {
    breakStorage();
    expect(() => {
      hostTokens.set("abcdefghijkl", TOKEN);
      hostTokens.remove("abcdefghijkl");
      hostedEvents.add(hosted(1));
      hostedEvents.rename(eventId(1), "x");
      hostedEvents.remove(eventId(1));
      guestName.set("Aisyah");
      guestName.set("");
    }).not.toThrow();
  });
});
