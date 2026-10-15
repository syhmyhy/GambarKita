import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { ApiError, api } from "../lib/api";
import { feedReducer, initialFeed } from "../lib/feed";
import { type ConnectionStatus, type GoneReason, LiveSocket } from "../lib/realtime";
import type { EventPublic, LiveMessage, Photo } from "../lib/types";

const PAGE_SIZE = 48;
const POLL_INTERVAL = 20_000; // fallback while the live connection is down
const MAX_TRACKED_ARRIVALS = 500;

export interface LiveFeed {
  event: EventPublic | null;
  photos: Photo[];
  status: ConnectionStatus;
  /** The event does not exist (any more). */
  gone: GoneReason | null;
  /** Loading the event failed for another reason (network, server). */
  error: unknown;
  initialLoading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => Promise<void>;
  /** Reload the event and the newest photos (e.g. a "Try again" button). */
  retry: () => void;
  /** Apply an event returned by a mutation (e.g. PATCH) without waiting for the socket. */
  applyEvent: (event: EventPublic) => void;
  /** Remove a photo locally right away (e.g. after the host deleted it). */
  removePhoto: (photoId: string) => void;
  /** Newest photo that arrived live (not from a page load), for "new photo" effects. */
  latestLiveId: string | null;
}

/**
 * Live photos for one event: subscribes to the WebSocket first, then loads the
 * first page (again after every reconnect), so nothing uploaded in between is
 * missed. Falls back to polling while the socket is down.
 */
export function useLiveFeed(eventId: string, options: { hostToken?: string | null } = {}): LiveFeed {
  const [feed, dispatch] = useReducer(feedReducer, initialFeed);
  const [event, setEvent] = useState<EventPublic | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [gone, setGone] = useState<GoneReason | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [latestLiveId, setLatestLiveId] = useState<string | null>(null);
  const hostToken = options.hostToken ?? null;

  // Bookkeeping touched only from callbacks (never during render):
  // - arrivals: live photo id -> arrival number, so a page requested before a
  //   photo arrived does not remove it;
  // - requests: order of first-page requests, so a slow, older response never
  //   overwrites a newer one;
  // - known: ids already counted in photo_count.
  const arrivals = useRef({ counter: 0, byId: new Map<string, number>() });
  const requests = useRef({ started: 0, applied: 0 });
  const known = useRef(new Set<string>());

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const request = ++requests.current.started;
      const liveBefore = arrivals.current.counter;
      try {
        const [details, page] = await Promise.all([
          api.getEvent(eventId, signal),
          api.listPhotos(eventId, { limit: PAGE_SIZE, signal }),
        ]);
        if (request < requests.current.applied) return; // a newer response already landed
        requests.current.applied = request;
        const keepIds = new Set<string>();
        for (const [id, arrival] of arrivals.current.byId) if (arrival > liveBefore) keepIds.add(id);
        for (const photo of page.items) known.current.add(photo.id);
        setEvent(details);
        dispatch({ type: "first-page", items: page.items, nextCursor: page.next_cursor, keepIds });
        setError(null);
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        if (caught instanceof ApiError && caught.code === "EVENT_NOT_FOUND") setGone("not_found");
        else setError(caught);
      } finally {
        if (!signal?.aborted) setInitialLoading(false);
      }
    },
    [eventId],
  );

  // Live connection (and a resync on every (re)connect). Pages are keyed by
  // event id, so this hook instance only ever serves one event.
  useEffect(() => {
    const controller = new AbortController();
    const onMessage = (message: LiveMessage) => {
      switch (message.type) {
        case "hello":
        case "event.updated":
          setEvent(message.data);
          break;
        case "photo.created": {
          const photo = message.data;
          const tracker = arrivals.current;
          tracker.byId.set(photo.id, ++tracker.counter);
          if (tracker.byId.size > MAX_TRACKED_ARRIVALS) {
            tracker.byId.delete(tracker.byId.keys().next().value as string);
          }
          dispatch({ type: "created", photo });
          setLatestLiveId(photo.id);
          if (!known.current.has(photo.id)) {
            known.current.add(photo.id);
            setEvent((current) => (current ? { ...current, photo_count: current.photo_count + 1 } : current));
          }
          break;
        }
        case "photo.deleted":
          dispatch({ type: "deleted", id: message.data.id });
          if (known.current.delete(message.data.id)) {
            setEvent((current) => (current ? { ...current, photo_count: Math.max(0, current.photo_count - 1) } : current));
          }
          break;
        case "event.deleted":
          setGone("deleted");
          break;
        default:
          break;
      }
    };
    const socket = new LiveSocket({
      eventId,
      hostToken,
      onMessage,
      onStatus: setStatus,
      onOpen: () => void refresh(controller.signal),
      onGone: setGone,
    });
    socket.start();
    // Don't wait for the socket to show something. State is only set once the
    // response arrives (asynchronously), which is what this effect is for.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh(controller.signal);
    return () => {
      controller.abort();
      socket.stop();
    };
  }, [eventId, hostToken, refresh]);

  // Polling fallback while the live connection is down (e.g. a proxy without WebSockets).
  useEffect(() => {
    if (status === "open" || gone) return;
    const timer = setInterval(() => void refresh(), POLL_INTERVAL);
    return () => clearInterval(timer);
  }, [status, gone, refresh]);

  const cursor = feed.nextCursor;
  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await api.listPhotos(eventId, { cursor, limit: PAGE_SIZE });
      for (const photo of page.items) known.current.add(photo.id);
      dispatch({ type: "older-page", items: page.items, nextCursor: page.next_cursor });
    } finally {
      setLoadingMore(false);
    }
  }, [eventId, cursor, loadingMore]);

  const retry = useCallback(() => {
    setError(null);
    void refresh();
  }, [refresh]);

  const applyEvent = useCallback((next: EventPublic) => setEvent(next), []);

  const removePhoto = useCallback((photoId: string) => {
    dispatch({ type: "deleted", id: photoId });
    if (known.current.delete(photoId)) {
      setEvent((current) => (current ? { ...current, photo_count: Math.max(0, current.photo_count - 1) } : current));
    }
  }, []);

  return {
    event,
    photos: feed.photos,
    status,
    gone,
    error,
    initialLoading,
    hasMore: typeof feed.nextCursor === "string",
    loadingMore,
    loadMore,
    retry,
    applyEvent,
    removePhoto,
    latestLiveId,
  };
}
