import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicConfig } from "../../lib/types";

const mocks = vi.hoisted(() => ({ getConfig: vi.fn<() => Promise<PublicConfig>>() }));

vi.mock("../../lib/api", () => ({ api: { getConfig: mocks.getConfig } }));

const SERVER_CONFIG: PublicConfig = {
  max_upload_bytes: 5 * 1024 * 1024,
  allowed_mime_types: ["image/jpeg"],
  max_photos_per_event: 100,
  max_photos_per_guest: 15,
  max_event_name_length: 60,
  max_uploader_name_length: 30,
  max_caption_length: 100,
  event_creation_requires_key: true,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// The hook caches its request at module level: load a fresh copy for every test.
async function loadHook() {
  vi.resetModules();
  return import("../usePublicConfig");
}

beforeEach(() => {
  mocks.getConfig.mockReset();
});

describe("usePublicConfig", () => {
  it("returns the defaults until the server answers, then the server's config", async () => {
    const { usePublicConfig, DEFAULT_CONFIG } = await loadHook();
    const response = deferred<PublicConfig>();
    mocks.getConfig.mockReturnValue(response.promise);

    const { result } = renderHook(() => usePublicConfig());
    expect(result.current).toEqual({ config: DEFAULT_CONFIG, loaded: false });

    await act(async () => response.resolve(SERVER_CONFIG));
    expect(result.current).toEqual({ config: SERVER_CONFIG, loaded: true });
  });

  it("defaults match the backend's documented limits", async () => {
    const { DEFAULT_CONFIG } = await loadHook();
    expect(DEFAULT_CONFIG.max_upload_bytes).toBe(10 * 1024 * 1024);
    expect(DEFAULT_CONFIG.allowed_mime_types).toEqual(["image/jpeg", "image/png", "image/webp"]);
    expect(DEFAULT_CONFIG.event_creation_requires_key).toBe(false);
  });

  it("shares one request between components and later mounts", async () => {
    const { usePublicConfig } = await loadHook();
    mocks.getConfig.mockResolvedValue(SERVER_CONFIG);

    const first = renderHook(() => usePublicConfig());
    const second = renderHook(() => usePublicConfig());
    await waitFor(() => expect(first.result.current.loaded).toBe(true));
    await waitFor(() => expect(second.result.current.loaded).toBe(true));
    first.unmount();
    second.unmount();

    const later = renderHook(() => usePublicConfig());
    await waitFor(() => expect(later.result.current.config).toEqual(SERVER_CONFIG));
    expect(mocks.getConfig).toHaveBeenCalledTimes(1);
  });

  it("keeps the defaults when the request fails, and retries on the next mount", async () => {
    const { usePublicConfig, DEFAULT_CONFIG } = await loadHook();
    mocks.getConfig.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(SERVER_CONFIG);

    const failed = renderHook(() => usePublicConfig());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(failed.result.current).toEqual({ config: DEFAULT_CONFIG, loaded: false });
    failed.unmount();

    const retried = renderHook(() => usePublicConfig());
    await waitFor(() => expect(retried.result.current.loaded).toBe(true));
    expect(retried.result.current.config).toEqual(SERVER_CONFIG);
    expect(mocks.getConfig).toHaveBeenCalledTimes(2);
  });

  it("ignores a response that arrives after unmounting", async () => {
    const { usePublicConfig } = await loadHook();
    const response = deferred<PublicConfig>();
    mocks.getConfig.mockReturnValue(response.promise);
    const consoleError = vi.spyOn(console, "error");

    const { result, unmount } = renderHook(() => usePublicConfig());
    unmount();
    await act(async () => response.resolve(SERVER_CONFIG));
    expect(result.current.loaded).toBe(false);
    expect(consoleError).not.toHaveBeenCalled();
  });
});
