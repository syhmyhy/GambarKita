import { afterEach, describe, expect, it, vi } from "vitest";

const HEX = "0123456789abcdef0123456789abcdef";
const PHOTO = `/media/events/abcdefghijkl/photos/${HEX}.jpg`;

/** Import a fresh copy of env.ts with VITE_API_BASE_URL set to `base`. */
async function loadEnv(base = "") {
  vi.resetModules();
  vi.stubEnv("VITE_API_BASE_URL", base);
  return import("../env");
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("mediaUrl", () => {
  it.each([
    PHOTO,
    `/media/events/abcdefghijkl/thumbs/${HEX}.webp`,
    `/media/events/0123456789ab/photos/${HEX}.png`,
  ])("accepts the server media path %s", async (path) => {
    const { mediaUrl } = await loadEnv();
    expect(mediaUrl(path)).toBe(path);
  });

  it.each([
    ["an empty string", ""],
    ["an absolute URL", `https://evil.example${PHOTO}`],
    ["a protocol-relative URL", `//evil.example${PHOTO}`],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:image/png;base64,iVBORw0KGgo="],
    ["path traversal", "/media/events/abcdefghijkl/photos/../../../../etc/passwd"],
    ["traversal before the id", `/media/events/../abcdefghijkl/photos/${HEX}.jpg`],
    ["encoded traversal", `/media/events/abcdefghijkl/photos/..%2F..%2F${HEX}.jpg`],
    ["a short event id", `/media/events/abcdefghijk/photos/${HEX}.jpg`],
    ["a long event id", `/media/events/abcdefghijklm/photos/${HEX}.jpg`],
    ["an upper-case event id", `/media/events/ABCDEFGHIJKL/photos/${HEX}.jpg`],
    ["an unknown folder", `/media/events/abcdefghijkl/originals/${HEX}.jpg`],
    ["a short photo id", `/media/events/abcdefghijkl/photos/${HEX.slice(1)}.jpg`],
    ["a non-hex photo id", `/media/events/abcdefghijkl/photos/${HEX.slice(1)}g.jpg`],
    ["an upper-case photo id", `/media/events/abcdefghijkl/photos/${HEX.toUpperCase()}.jpg`],
    ["an svg", `/media/events/abcdefghijkl/photos/${HEX}.svg`],
    ["a gif", `/media/events/abcdefghijkl/photos/${HEX}.gif`],
    ["an upper-case extension", `/media/events/abcdefghijkl/photos/${HEX}.JPG`],
    ["a .jpeg extension", `/media/events/abcdefghijkl/photos/${HEX}.jpeg`],
    ["a double extension", `/media/events/abcdefghijkl/photos/${HEX}.jpg.html`],
    ["a query string", `${PHOTO}?download=1`],
    ["a fragment", `${PHOTO}#x`],
    ["a trailing newline", `${PHOTO}\n`],
    ["leading whitespace", ` ${PHOTO}`],
    ["an API path", `/api/events/abcdefghijkl/photos/${HEX}.jpg`],
    ["a missing leading slash", PHOTO.slice(1)],
  ])("rejects %s", async (_label, path) => {
    const { mediaUrl } = await loadEnv();
    expect(mediaUrl(path)).toBe("");
  });

  it("prefixes the API base when one is configured", async () => {
    const { mediaUrl } = await loadEnv("https://api.example.com");
    expect(mediaUrl(PHOTO)).toBe(`https://api.example.com${PHOTO}`);
    expect(mediaUrl("https://evil.example/x.jpg")).toBe("");
  });
});

describe("API_BASE and apiUrl", () => {
  it("defaults to the same origin", async () => {
    const { API_BASE, apiUrl } = await loadEnv();
    expect(API_BASE).toBe("");
    expect(apiUrl("/api/events")).toBe("/api/events");
    expect(apiUrl("api/events")).toBe("/api/events");
  });

  it("trims whitespace and trailing slashes from the configured base", async () => {
    const { API_BASE, apiUrl } = await loadEnv("  https://api.example.com//  ");
    expect(API_BASE).toBe("https://api.example.com");
    expect(apiUrl("/api/config")).toBe("https://api.example.com/api/config");
  });
});

describe("wsUrl", () => {
  it("uses wss: on an https page", async () => {
    const { wsUrl } = await loadEnv();
    expect(wsUrl("/ws/events/x", { protocol: "https:", host: "photos.example.com" })).toBe("wss://photos.example.com/ws/events/x");
  });

  it("uses ws: on an http page and keeps the port", async () => {
    const { wsUrl } = await loadEnv();
    expect(wsUrl("/ws/events/x", { protocol: "http:", host: "192.168.1.20:5173" })).toBe("ws://192.168.1.20:5173/ws/events/x");
  });

  it("defaults to the page's own location", async () => {
    const { wsUrl } = await loadEnv();
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    expect(wsUrl("/ws/events/x")).toBe(`${scheme}//${window.location.host}/ws/events/x`);
  });

  it("derives the socket origin from a configured API base", async () => {
    const secure = await loadEnv("https://api.example.com");
    expect(secure.wsUrl("/ws/events/x", { protocol: "http:", host: "ignored" })).toBe("wss://api.example.com/ws/events/x");
    const plain = await loadEnv("http://127.0.0.1:8000/");
    expect(plain.wsUrl("/ws/events/x")).toBe("ws://127.0.0.1:8000/ws/events/x");
  });
});

describe("routes", () => {
  it("builds the app's paths", async () => {
    const { routes } = await loadEnv();
    expect(routes.home()).toBe("/");
    expect(routes.upload("abcdefghijkl")).toBe("/e/abcdefghijkl");
    expect(routes.wall("abcdefghijkl")).toBe("/e/abcdefghijkl/wall");
    expect(routes.host("abcdefghijkl")).toBe("/e/abcdefghijkl/host");
  });

  it("absoluteAppUrl resolves a route against this origin", async () => {
    const { absoluteAppUrl, routes } = await loadEnv();
    expect(absoluteAppUrl(routes.upload("abcdefghijkl"))).toBe(`${window.location.origin}/e/abcdefghijkl`);
  });

  it("EVENT_ID_PATTERN accepts exactly 12 lower-case letters and digits", async () => {
    const { EVENT_ID_PATTERN } = await loadEnv();
    expect(EVENT_ID_PATTERN.test("abcdefghijkl")).toBe(true);
    expect(EVENT_ID_PATTERN.test("0123456789ab")).toBe(true);
    for (const bad of ["", "abcdefghijk", "abcdefghijklm", "ABCDEFGHIJKL", "abcdefghijk-", "abcdefghijk\n"]) {
      expect(EVENT_ID_PATTERN.test(bad)).toBe(false);
    }
  });
});
