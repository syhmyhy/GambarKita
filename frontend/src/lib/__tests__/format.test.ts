import { describe, expect, it } from "vitest";

import { formatBytes, pluralize, timeAgo } from "../format";

describe("formatBytes", () => {
  it.each([
    [0, "0 bytes"],
    [512, "512 bytes"],
    [1023, "1023 bytes"],
    [1024, "1 KB"],
    [1536, "1.5 KB"],
    [100 * 1024, "100 KB"],
    [1024 * 1024, "1 MB"],
    [10 * 1024 * 1024, "10 MB"],
    [2.5 * 1024 * 1024, "2.5 MB"],
    [1024 * 1024 * 1024, "1024 MB"],
  ])("%i -> %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("timeAgo", () => {
  // Same formatter settings as format.ts, so expectations hold in any locale.
  const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const NOW = Date.UTC(2026, 8, 28, 12, 0, 0);
  const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

  it("says 'just now' within 45 seconds either way", () => {
    expect(timeAgo(ago(0), NOW)).toBe("just now");
    expect(timeAgo(ago(44), NOW)).toBe("just now");
    expect(timeAgo(ago(-30), NOW)).toBe("just now");
  });

  it("rounds 45-59 seconds to a minute", () => {
    expect(timeAgo(ago(50), NOW)).toBe(relative.format(-1, "minute"));
  });

  it.each([
    [5 * 60, -5, "minute"],
    [59 * 60, -59, "minute"],
    [2 * 3600, -2, "hour"],
    [24 * 3600, -1, "day"],
    [3 * 24 * 3600, -3, "day"],
    [14 * 24 * 3600, -2, "week"],
    [60 * 24 * 3600, -2, "month"],
    [800 * 24 * 3600, -2, "year"],
  ] as const)("%i seconds ago -> format(%i, %s)", (seconds, value, unit) => {
    expect(timeAgo(ago(seconds), NOW)).toBe(relative.format(value, unit));
  });

  it("handles future times", () => {
    expect(timeAgo(ago(-3 * 3600), NOW)).toBe(relative.format(3, "hour"));
  });

  it("returns an empty string for an invalid date", () => {
    expect(timeAgo("not a date", NOW)).toBe("");
  });

  it("uses the current time by default", () => {
    expect(timeAgo(new Date().toISOString())).toBe("just now");
  });
});

describe("pluralize", () => {
  it("uses the singular for exactly one", () => {
    expect(pluralize(1, "photo")).toBe("1 photo");
  });

  it("adds an s otherwise", () => {
    expect(pluralize(0, "photo")).toBe("0 photos");
    expect(pluralize(2, "photo")).toBe("2 photos");
  });

  it("accepts an irregular plural", () => {
    expect(pluralize(3, "person", "people")).toBe("3 people");
    expect(pluralize(1, "person", "people")).toBe("1 person");
  });

  it("formats large counts for the locale", () => {
    expect(pluralize(1234, "photo")).toBe(`${(1234).toLocaleString()} photos`);
  });
});
