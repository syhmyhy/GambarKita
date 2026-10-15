import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EVENT_ID, makeEvent, makePhoto as photo } from "../../lib/__tests__/fixtures";
import { ApiError, networkError } from "../../lib/api";
import type { LiveSocketOptions } from "../../lib/realtime";
import type { EventPublic, LiveMessage, PhotoPage } from "../../lib/types";
import { useLiveFeed } from "../useLiveFeed";

const mocks = vi.hoisted(() => {
  const sockets: FakeLiveSocket[] = [];
  class FakeLiveSocket {
    readonly options: LiveSocketOptions;
    readonly start = vi.fn<() => void>();
    readonly stop = vi.fn<() => void>();
    constructor(options: LiveSocketOptions) {
      this.options = options;
      sockets.push(this);
    }
  }
  return {
    getEvent: vi.fn<(eventId: string, signal?: AbortSignal) => Promise<EventPublic>>(),
    listPhotos:
      vi.fn<(eventId: string, options?: { cursor?: string | null; limit?: number; signal?: AbortSignal }) => Promise<PhotoPage>>(),
    sockets,
    FakeLiveSocket,
  };
});

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return { ...actual, api: { ...actual.api, getEvent: mocks.getEvent, listPhotos: mocks.listPhotos } };
});

vi.mock("../../lib/realtime", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/realtime")>();
  return { ...actual, LiveSocket: mocks.FakeLiveSocket };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function socket() {
  const current = mocks.sockets.at(-1);
  if (!current) throw new Error("No LiveSocket was created");
  return current;
}

function push(message: LiveMessage): void {
  act(() => socket().options.onMessage(message));
}

function renderFeed(hostToken?: string | null) {
  return renderHook((props: { id: string; token?: string | null }) => useLiveFeed(props.id, { hostToken: props.token }), {
    initialProps: { id: EVENT_ID, token: hostToken },
  });
}

async function renderLoadedFeed(hostToken?: string | null) {
  const view = renderFeed(hostToken);
  await waitFor(() => expect(view.result.current.initialLoading).toBe(false));
  return view;
}

const ids = (photos: { id: string }[]) => photos.map((item) => item.id);

beforeEach(() => {
  mocks.sockets.length = 0;
  mocks.getEvent.mockReset();
  mocks.listPhotos.mockReset();
  mocks.getEvent.mockImplementation(async () => makeEvent({ photo_count: 2 }));
  mocks.listPhotos.mockImplementation(async () => ({ items: [photo("p2", 2), photo("p1", 1)], next_cursor: null }));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useLiveFeed initial load", () => {
  it("starts empty and loading, then shows the event and its first page", async () => {
    const { result } = renderFeed();
    expect(result.current).toMatchObject({
      event: null,
      photos: [],
      status: "connecting",
      gone: null,
      error: null,
      initialLoading: true,
      hasMore: false,
      loadingMore: false,
      latestLiveId: null,
    });

    await waitFor(() => expect(result.current.initialLoading).toBe(false));
    expect(result.current.event).toEqual(makeEvent({ photo_count: 2 }));
    expect(ids(result.current.photos)).toEqual(["p2", "p1"]);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.error).toBeNull();
    expect(mocks.getEvent).toHaveBeenCalledWith(EVENT_ID, expect.any(AbortSignal));
    expect(mocks.listPhotos).toHaveBeenCalledWith(EVENT_ID, { limit: 48, signal: expect.any(AbortSignal) });
  });

  it("reports more photos when the first page has a cursor", async () => {
    mocks.listPhotos.mockResolvedValue({ items: [photo("p2", 2)], next_cursor: "cursor-1" });
    const { result } = await renderLoadedFeed();
    expect(result.current.hasMore).toBe(true);
  });

  it("marks a missing event as gone (not_found)", async () => {
    mocks.getEvent.mockRejectedValue(new ApiError({ status: 404, code: "EVENT_NOT_FOUND", message: "Event not found." }));
    const { result } = await renderLoadedFeed();
    expect(result.current.gone).toBe("not_found");
    expect(result.current.error).toBeNull();
    expect(result.current.event).toBeNull();
  });

  it("exposes other failures as error and clears it after a successful resync", async () => {
    const failure = networkError();
    mocks.getEvent.mockRejectedValueOnce(failure);
    const { result } = await renderLoadedFeed();
    expect(result.current.error).toBe(failure);
    expect(result.current.gone).toBeNull();

    await act(async () => socket().options.onOpen?.());
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(result.current.event?.id).toBe(EVENT_ID);
  });

  it("subscribes to the event's live socket without a host token by default", () => {
    renderFeed();
    expect(mocks.sockets).toHaveLength(1);
    expect(socket().options.eventId).toBe(EVENT_ID);
    expect(socket().options.hostToken).toBeNull();
    expect(socket().start).toHaveBeenCalledTimes(1);
  });

  it("passes the host token to the socket and re-subscribes when it changes", () => {
    const token = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFy";
    const { rerender } = renderFeed(null);
    const first = socket();
    rerender({ id: EVENT_ID, token });
    expect(first.stop).toHaveBeenCalledTimes(1);
    expect(mocks.sockets).toHaveLength(2);
    expect(socket().options.hostToken).toBe(token);
  });

  it("stops the socket and aborts requests on unmount", () => {
    mocks.getEvent.mockReturnValue(new Promise<EventPublic>(() => undefined));
    const { unmount } = renderFeed();
    const signal = mocks.getEvent.mock.calls[0]?.[1];
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(socket().stop).toHaveBeenCalledTimes(1);
    expect(signal?.aborted).toBe(true);
  });
});

describe("useLiveFeed live updates", () => {
  it("forwards the connection status", async () => {
    const { result } = await renderLoadedFeed();
    act(() => socket().options.onStatus?.("open"));
    expect(result.current.status).toBe("open");
    act(() => socket().options.onStatus?.("reconnecting"));
    expect(result.current.status).toBe("reconnecting");
  });

  it("adds a live photo, counts it and marks it as the latest live photo", async () => {
    const { result } = await renderLoadedFeed();
    push({ type: "photo.created", event_id: EVENT_ID, data: photo("p3", 3) });
    expect(ids(result.current.photos)).toEqual(["p3", "p2", "p1"]);
    expect(result.current.latestLiveId).toBe("p3");
    expect(result.current.event?.photo_count).toBe(3);
  });

  it("removes a deleted photo and uncounts it", async () => {
    const { result } = await renderLoadedFeed();
    push({ type: "photo.deleted", event_id: EVENT_ID, data: { id: "p2" } });
    expect(ids(result.current.photos)).toEqual(["p1"]);
    expect(result.current.event?.photo_count).toBe(1);
  });

  it("never counts below zero", async () => {
    mocks.getEvent.mockResolvedValue(makeEvent({ photo_count: 0 }));
    mocks.listPhotos.mockResolvedValue({ items: [], next_cursor: null });
    const { result } = await renderLoadedFeed();
    push({ type: "photo.deleted", event_id: EVENT_ID, data: { id: "ghost" } });
    expect(result.current.event?.photo_count).toBe(0);
  });

  it("does not re-add a deleted photo from a later resync", async () => {
    const { result } = await renderLoadedFeed();
    push({ type: "photo.deleted", event_id: EVENT_ID, data: { id: "p2" } });
    await act(async () => socket().options.onOpen?.()); // the page still lists p2 (stale)
    await waitFor(() => expect(mocks.listPhotos).toHaveBeenCalledTimes(2));
    expect(ids(result.current.photos)).toEqual(["p1"]);
  });

  it.each(["hello", "event.updated"] as const)("replaces the event on %s", async (type) => {
    const { result } = await renderLoadedFeed();
    const updated = makeEvent({ name: "Renamed", uploads_open: false, photo_count: 2 });
    push({ type, event_id: EVENT_ID, data: updated });
    expect(result.current.event).toEqual(updated);
  });

  it("marks the event as deleted on event.deleted", async () => {
    const { result } = await renderLoadedFeed();
    push({ type: "event.deleted", event_id: EVENT_ID });
    expect(result.current.gone).toBe("deleted");
  });

  it("marks the event as gone when the socket says so", async () => {
    const { result } = await renderLoadedFeed();
    act(() => socket().options.onGone?.("not_found"));
    expect(result.current.gone).toBe("not_found");
  });

  it("ignores pong messages", async () => {
    const { result } = await renderLoadedFeed();
    const before = result.current;
    push({ type: "pong" });
    expect(result.current.event).toBe(before.event);
    expect(result.current.photos).toBe(before.photos);
  });

  it("resynchronises on every (re)connect", async () => {
    const { result } = await renderLoadedFeed();
    mocks.getEvent.mockResolvedValue(makeEvent({ name: "Renamed", photo_count: 3 }));
    mocks.listPhotos.mockResolvedValue({ items: [photo("p3", 3), photo("p2", 2), photo("p1", 1)], next_cursor: null });

    await act(async () => socket().options.onOpen?.());
    await waitFor(() => expect(ids(result.current.photos)).toEqual(["p3", "p2", "p1"]));
    expect(result.current.event?.name).toBe("Renamed");
    expect(mocks.getEvent).toHaveBeenCalledTimes(2);
    expect(result.current.latestLiveId).toBeNull(); // page loads are not "live" arrivals
  });

  it("removePhoto removes a photo locally", async () => {
    const { result } = await renderLoadedFeed();
    act(() => result.current.removePhoto("p1"));
    expect(ids(result.current.photos)).toEqual(["p2"]);
  });

  // BUG (useLiveFeed.ts + feed.ts): a first page is treated as authoritative for
  // everything newer than its oldest item, even photos that arrived over the
  // socket after the page's snapshot was taken. A slow resync (or the initial
  // load racing the onOpen resync) therefore removes live photos from the wall
  // until the next reconnect.
  it("a resync requested before a live photo arrived does not drop that photo", async () => {
    const { result } = await renderLoadedFeed();
    const page = deferred<PhotoPage>();
    mocks.listPhotos.mockReturnValueOnce(page.promise);

    await act(async () => socket().options.onOpen?.()); // resync starts
    push({ type: "photo.created", event_id: EVENT_ID, data: photo("p3", 3) }); // arrives meanwhile
    expect(ids(result.current.photos)).toContain("p3");

    // The page's snapshot predates p3.
    await act(async () => page.resolve({ items: [photo("p2", 2), photo("p1", 1)], next_cursor: null }));
    expect(ids(result.current.photos)).toContain("p3");
  });

  // BUG (useLiveFeed.ts): photo_count is incremented for every photo.created,
  // even when the photo is already in the feed (the push raced with a page that
  // already included it), so the counter overshoots until the next resync.
  it("a live push for a photo already on the page does not count it twice", async () => {
    const { result } = await renderLoadedFeed();
    push({ type: "photo.created", event_id: EVENT_ID, data: photo("p2", 2) });
    expect(result.current.photos).toHaveLength(2);
    expect(result.current.event?.photo_count).toBe(2);
  });
});

describe("useLiveFeed loadMore", () => {
  it("loads the next page with the cursor and appends it", async () => {
    mocks.listPhotos.mockResolvedValueOnce({ items: [photo("p4", 4), photo("p3", 3)], next_cursor: "cursor-1" });
    const { result } = await renderLoadedFeed();
    expect(result.current.hasMore).toBe(true);

    const older = deferred<PhotoPage>();
    mocks.listPhotos.mockReturnValueOnce(older.promise);
    let loading!: Promise<void>;
    act(() => {
      loading = result.current.loadMore();
    });
    expect(result.current.loadingMore).toBe(true);
    expect(mocks.listPhotos).toHaveBeenLastCalledWith(EVENT_ID, { cursor: "cursor-1", limit: 48 });

    await act(async () => {
      older.resolve({ items: [photo("p2", 2), photo("p1", 1)], next_cursor: null });
      await loading;
    });
    expect(ids(result.current.photos)).toEqual(["p4", "p3", "p2", "p1"]);
    expect(result.current.loadingMore).toBe(false);
    expect(result.current.hasMore).toBe(false);
  });

  it("follows cursors page after page", async () => {
    mocks.listPhotos
      .mockResolvedValueOnce({ items: [photo("p3", 3)], next_cursor: "cursor-1" })
      .mockResolvedValueOnce({ items: [photo("p2", 2)], next_cursor: "cursor-2" })
      .mockResolvedValueOnce({ items: [photo("p1", 1)], next_cursor: null });
    const { result } = await renderLoadedFeed();

    await act(() => result.current.loadMore());
    expect(mocks.listPhotos).toHaveBeenLastCalledWith(EVENT_ID, { cursor: "cursor-1", limit: 48 });
    await act(() => result.current.loadMore());
    expect(mocks.listPhotos).toHaveBeenLastCalledWith(EVENT_ID, { cursor: "cursor-2", limit: 48 });
    expect(ids(result.current.photos)).toEqual(["p3", "p2", "p1"]);
  });

  it("does nothing when there is no cursor", async () => {
    const { result } = await renderLoadedFeed(); // next_cursor: null
    await act(() => result.current.loadMore());
    expect(mocks.listPhotos).toHaveBeenCalledTimes(1);
  });

  it("does nothing before the first page has loaded", async () => {
    mocks.listPhotos.mockReturnValue(new Promise<PhotoPage>(() => undefined));
    const { result } = renderFeed();
    await act(() => result.current.loadMore());
    expect(mocks.listPhotos).toHaveBeenCalledTimes(1);
  });

  it("ignores calls while a page is loading", async () => {
    mocks.listPhotos.mockResolvedValueOnce({ items: [photo("p3", 3)], next_cursor: "cursor-1" });
    const { result } = await renderLoadedFeed();
    mocks.listPhotos.mockReturnValueOnce(new Promise<PhotoPage>(() => undefined));
    act(() => void result.current.loadMore());
    expect(result.current.loadingMore).toBe(true);
    act(() => void result.current.loadMore());
    expect(mocks.listPhotos).toHaveBeenCalledTimes(2);
  });

  it("rejects and resets loadingMore when the page fails", async () => {
    mocks.listPhotos.mockResolvedValueOnce({ items: [photo("p3", 3)], next_cursor: "cursor-1" });
    const { result } = await renderLoadedFeed();
    const failure = networkError();
    mocks.listPhotos.mockRejectedValueOnce(failure);

    let caught: unknown;
    await act(async () => {
      await result.current.loadMore().catch((error: unknown) => {
        caught = error;
      });
    });
    expect(caught).toBe(failure);
    expect(result.current.loadingMore).toBe(false);
    expect(result.current.hasMore).toBe(true);
  });
});

describe("useLiveFeed polling fallback", () => {
  it("polls every 20 seconds only while the live connection is down", async () => {
    vi.useFakeTimers();
    renderFeed();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(mocks.getEvent).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(mocks.getEvent).toHaveBeenCalledTimes(2);

    act(() => socket().options.onStatus?.("open"));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(mocks.getEvent).toHaveBeenCalledTimes(2);

    act(() => socket().options.onStatus?.("reconnecting"));
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(mocks.getEvent).toHaveBeenCalledTimes(3);
  });

  it("stops polling once the event is gone", async () => {
    vi.useFakeTimers();
    renderFeed();
    await act(() => vi.advanceTimersByTimeAsync(0));
    act(() => socket().options.onGone?.("deleted"));
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(mocks.getEvent).toHaveBeenCalledTimes(1);
  });
});
