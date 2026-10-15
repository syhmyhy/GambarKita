import type { Photo } from "./types";

/**
 * Pure state machine for a live photo feed (newest first). It combines REST
 * pages with WebSocket pushes, which can arrive in any order:
 *
 * - photos are de-duplicated by id and kept sorted newest first;
 * - a fresh first page after a (re)connect is authoritative for the time range
 *   it covers, so photos deleted while offline disappear, except photos that
 *   arrived live after the page was requested (`keepIds`): the page's
 *   snapshot may predate them;
 * - if the page does not reach the photos already loaded (more than a page was
 *   uploaded while offline), its own cursor is used so the gap can be loaded;
 * - a photo deleted over the socket is never re-added by a stale page.
 */
export interface FeedState {
  photos: Photo[];
  /** Cursor for older photos: undefined = not loaded yet, null = no more. */
  nextCursor: string | null | undefined;
  deletedIds: ReadonlySet<string>;
}

export type FeedAction =
  | { type: "first-page"; items: Photo[]; nextCursor: string | null; keepIds?: ReadonlySet<string> }
  | { type: "older-page"; items: Photo[]; nextCursor: string | null }
  | { type: "created"; photo: Photo }
  | { type: "deleted"; id: string }
  | { type: "reset" };

export const initialFeed: FeedState = { photos: [], nextCursor: undefined, deletedIds: new Set() };

const MAX_REMEMBERED_DELETIONS = 500;

function timestamp(photo: Photo): number {
  const value = Date.parse(photo.created_at);
  return Number.isFinite(value) ? value : 0;
}

function newestFirst(a: Photo, b: Photo): number {
  return timestamp(b) - timestamp(a) || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0);
}

function merge(existing: Photo[], incoming: Photo[], deleted: ReadonlySet<string>): Photo[] {
  const byId = new Map(existing.map((photo) => [photo.id, photo]));
  for (const photo of incoming) {
    if (!deleted.has(photo.id)) byId.set(photo.id, photo);
  }
  return [...byId.values()].sort(newestFirst);
}

export function feedReducer(state: FeedState, action: FeedAction): FeedState {
  switch (action.type) {
    case "first-page": {
      const items = action.items.filter((photo) => !state.deletedIds.has(photo.id));
      const pageIds = new Set(action.items.map((photo) => photo.id));
      const arrivedLive = (photo: Photo) => action.keepIds?.has(photo.id) ?? false;
      let kept: Photo[];
      let nextCursor: string | null | undefined;
      if (action.nextCursor === null) {
        // The page is the complete list (plus anything that arrived live meanwhile).
        kept = state.photos.filter((photo) => !pageIds.has(photo.id) && arrivedLive(photo));
        nextCursor = null;
      } else {
        // Keep local photos older than everything the page covers.
        const oldest = Math.min(...action.items.map(timestamp));
        kept = state.photos.filter((photo) => !pageIds.has(photo.id) && (timestamp(photo) < oldest || arrivedLive(photo)));
        const overlaps = state.photos.some((photo) => pageIds.has(photo.id));
        // Without overlap there may be unseen photos between the page and what is
        // loaded: continue from the page's own cursor (the merge de-duplicates).
        nextCursor = state.nextCursor === undefined || !overlaps ? action.nextCursor : state.nextCursor;
      }
      return { ...state, photos: merge(kept, items, state.deletedIds), nextCursor };
    }
    case "older-page":
      return { ...state, photos: merge(state.photos, action.items, state.deletedIds), nextCursor: action.nextCursor };
    case "created":
      if (state.deletedIds.has(action.photo.id)) return state;
      return { ...state, photos: merge(state.photos, [action.photo], state.deletedIds) };
    case "deleted": {
      const deletedIds = new Set(state.deletedIds);
      deletedIds.add(action.id);
      if (deletedIds.size > MAX_REMEMBERED_DELETIONS) {
        deletedIds.delete(deletedIds.values().next().value as string);
      }
      return { ...state, photos: state.photos.filter((photo) => photo.id !== action.id), deletedIds };
    }
    case "reset":
      return initialFeed;
  }
}
