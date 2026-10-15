import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { api } from "../../../lib/api";
import type { GuestQuota } from "../../../lib/types";
import { useGuestQuota } from "../useGuestQuota";

vi.mock("../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/api")>();
  return { ...actual, api: { getGuestQuota: vi.fn() } };
});

const EVENT_ID = "abcdef123456";

function returningGuest() {
  window.localStorage.setItem(`gk.guest.${EVENT_ID}`, "k".repeat(43));
}

function deferredQuota() {
  let answer: (quota: GuestQuota) => void = () => undefined;
  vi.mocked(api.getGuestQuota).mockImplementation(() => new Promise((resolve) => (answer = resolve)));
  return (quota: GuestQuota) => answer(quota);
}

describe("useGuestQuota", () => {
  it("knows nothing, and asks nothing, without a configured limit", () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, null));
    expect(result.current).toMatchObject({ limit: null, remaining: null, pending: false });
    expect(api.getGuestQuota).not.toHaveBeenCalled();
  });

  it("follows the configured limit once it arrives, for a first-time guest", () => {
    const { result, rerender } = renderHook((props: { limit: number | null }) => useGuestQuota(EVENT_ID, props.limit), {
      initialProps: { limit: null as number | null },
    });
    rerender({ limit: 4 });
    expect(result.current).toMatchObject({ limit: 4, remaining: 4, pending: false });
    act(() => result.current.recordUpload());
    expect(result.current.remaining).toBe(3);
  });

  it("doesn't count twice a photo that lands while the count is being refreshed", async () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 5));
    act(() => result.current.recordUpload());
    expect(result.current.remaining).toBe(4);

    const answer = deferredQuota();
    let refreshed: Promise<GuestQuota> = Promise.resolve({ limit: null, used: 0, remaining: null });
    act(() => {
      refreshed = result.current.refresh();
    });
    // A photo finishes before the server answers; the server's count includes it.
    act(() => result.current.recordUpload());
    await act(async () => {
      answer({ limit: 5, used: 2, remaining: 3 });
      await refreshed;
    });
    expect(result.current.remaining).toBe(3);

    act(() => result.current.recordUpload());
    expect(result.current.remaining).toBe(2);
  });

  it("starts over from the server's answer after a refusal, when refreshed", async () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 3));
    act(() => result.current.markReached(3));
    expect(result.current.remaining).toBe(0);

    vi.mocked(api.getGuestQuota).mockResolvedValue({ limit: 3, used: 2, remaining: 1 });
    await act(async () => {
      await expect(result.current.refresh()).resolves.toEqual({ limit: 3, used: 2, remaining: 1 });
    });
    expect(result.current).toMatchObject({ limit: 3, remaining: 1 });
  });

  it("follows a limit the server switched off", async () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 3));
    vi.mocked(api.getGuestQuota).mockResolvedValue({ limit: null, used: 0, remaining: null });
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current).toMatchObject({ limit: null, remaining: null });
  });

  it("lets a failed refresh reject, leaving the allowance as it was", async () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 3));
    vi.mocked(api.getGuestQuota).mockRejectedValue(new Error("offline"));
    await act(async () => {
      await expect(result.current.refresh()).rejects.toThrow("offline");
    });
    expect(result.current).toMatchObject({ limit: 3, remaining: 3 });
  });

  it("never goes below zero", async () => {
    returningGuest();
    vi.mocked(api.getGuestQuota).mockResolvedValue({ limit: 2, used: 5, remaining: 0 });
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 2));
    await waitFor(() => expect(result.current.pending).toBe(false));
    expect(result.current.remaining).toBe(0);
  });

  it("is reached when the server says so, keeping the limit it reported", () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, null));
    act(() => result.current.markReached(7));
    expect(result.current).toMatchObject({ limit: 7, remaining: 0 });
  });

  it("falls back to the known limit when the refusal didn't carry one", () => {
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 3));
    act(() => result.current.markReached(null));
    expect(result.current).toMatchObject({ limit: 3, remaining: 0 });
  });

  it("stops waiting, without guessing a count, when the request fails", async () => {
    returningGuest();
    vi.mocked(api.getGuestQuota).mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useGuestQuota(EVENT_ID, 3));
    await waitFor(() => expect(result.current.pending).toBe(false));
    expect(result.current).toMatchObject({ limit: null, remaining: null });
  });
});
