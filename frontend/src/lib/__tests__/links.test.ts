import { describe, expect, it } from "vitest";

import { guestUploadUrl, shareableEventUrl } from "../env";

const ID = "k3x9p2m7q1ab";
const event = (upload: string) => ({ id: ID, links: { upload } });
const at = (origin: string) => ({ origin, hostname: new URL(origin).hostname });

describe("guestUploadUrl", () => {
  it("uses this page's own origin on a real host", () => {
    expect(guestUploadUrl(event("http://192.168.1.20:5173/e/" + ID), at("https://photos.example.com"))).toBe(
      `https://photos.example.com/e/${ID}`,
    );
  });

  it.each(["http://localhost:5173", "http://127.0.0.1:5173", "http://[::1]:5173"])(
    "prefers the server's public link when the page runs on %s",
    (origin) => {
      expect(guestUploadUrl(event(`http://192.168.1.20:5173/e/${ID}`), at(origin))).toBe(`http://192.168.1.20:5173/e/${ID}`);
    },
  );

  it.each([
    ["a loopback server link", `http://localhost:5173/e/${ID}`],
    ["another event's path", "http://192.168.1.20:5173/e/zzzzzzzzzzzz"],
    ["a non-http scheme", `javascript:alert(1)//e/${ID}`],
    ["garbage", "not a url"],
  ])("falls back to the page origin for %s", (_label, upload) => {
    expect(guestUploadUrl(event(upload), at("http://localhost:5173"))).toBe(`http://localhost:5173/e/${ID}`);
  });
});

describe("shareableEventUrl", () => {
  it("builds other routes on the same base as the guest link", () => {
    expect(shareableEventUrl(event(`http://192.168.1.20:5173/e/${ID}`), `/e/${ID}/host`, at("http://localhost:5173"))).toBe(
      `http://192.168.1.20:5173/e/${ID}/host`,
    );
  });
});
