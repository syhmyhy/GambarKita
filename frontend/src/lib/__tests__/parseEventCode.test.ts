import { describe, expect, it } from "vitest";

import { parseEventCode } from "../../pages/HomePage";

describe("parseEventCode", () => {
  it.each([
    ["a bare code", "k3x9p2m7q1ab", "k3x9p2m7q1ab"],
    ["an upper-case code", "K3X9P2M7Q1AB", "k3x9p2m7q1ab"],
    ["a code with surrounding whitespace", "  k3x9p2m7q1ab \n", "k3x9p2m7q1ab"],
    ["an upload link", "https://gambarkita.example/e/k3x9p2m7q1ab", "k3x9p2m7q1ab"],
    ["a wall link", "https://gambarkita.example/e/k3x9p2m7q1ab/wall", "k3x9p2m7q1ab"],
    ["a host link", "http://192.168.1.20:5173/e/k3x9p2m7q1ab/host", "k3x9p2m7q1ab"],
    ["a link with a query", "https://gambarkita.example/e/k3x9p2m7q1ab?ref=qr", "k3x9p2m7q1ab"],
    ["a link with a fragment", "https://gambarkita.example/e/k3x9p2m7q1ab#top", "k3x9p2m7q1ab"],
    ["an upper-case link", "HTTPS://GAMBARKITA.EXAMPLE/E/K3X9P2M7Q1AB", "k3x9p2m7q1ab"],
    ["a path without a host", "/e/k3x9p2m7q1ab", "k3x9p2m7q1ab"],
  ])("accepts %s", (_label, input, expected) => {
    expect(parseEventCode(input)).toBe(expected);
  });

  it.each([
    ["an empty string", ""],
    ["whitespace", "   "],
    ["a short code", "k3x9p2m7q1a"],
    ["a long code", "k3x9p2m7q1abc"],
    ["a code with punctuation", "k3x9p2m7q1a!"],
    ["a code with a dash", "k3x9p2-7q1ab"],
    ["a code with inner spaces", "k3x9 p2m7 q1ab"],
    ["a link with a short code", "https://gambarkita.example/e/k3x9p2m7q1a"],
    ["a link with a long code", "https://gambarkita.example/e/k3x9p2m7q1abc"],
    ["a link to another page", "https://gambarkita.example/about"],
    ["a random sentence", "see you at the party"],
  ])("rejects %s", (_label, input) => {
    expect(parseEventCode(input)).toBeNull();
  });
});
