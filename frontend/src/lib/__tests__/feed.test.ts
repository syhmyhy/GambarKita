import { describe, expect, it } from "vitest";

import { type FeedAction, type FeedState, feedReducer, initialFeed } from "../feed";
import { makePhoto as photo } from "./fixtures";

function run(...actions: FeedAction[]): FeedState {
  return actions.reduce(feedReducer, initialFeed);
}

function ids(state: FeedState): string[] {
  return state.photos.map((item) => item.id);
}

describe("feedReducer ordering", () => {
  it("sorts photos newest first by created_at", () => {
    const state = run({
      type: "first-page",
      items: [photo("b", 2), photo("d", 4), photo("a", 1), photo("c", 3)],
      nextCursor: null,
    });
    expect(ids(state)).toEqual(["d", "c", "b", "a"]);
  });

  it("breaks created_at ties by id, highest id first", () => {
    const state = run({
      type: "first-page",
      items: [photo("a", 5), photo("c", 5), photo("b", 5), photo("z", 1)],
      nextCursor: null,
    });
    expect(ids(state)).toEqual(["c", "b", "a", "z"]);
  });

  it("keeps the order stable whatever order the same photos arrive in", () => {
    const items = [photo("a", 5), photo("c", 5), photo("b", 6), photo("d", 4)];
    const forward = run({ type: "first-page", items, nextCursor: null });
    const backward = run({ type: "first-page", items: [...items].reverse(), nextCursor: null });
    expect(ids(forward)).toEqual(ids(backward));
  });

  it("sorts photos with an unparseable created_at last", () => {
    const state = run({
      type: "first-page",
      items: [photo("broken", 0, { created_at: "not a date" }), photo("a", 1), photo("b", 2)],
      nextCursor: null,
    });
    expect(ids(state)).toEqual(["b", "a", "broken"]);
  });

  it("places a live photo by its timestamp, not at the top", () => {
    const state = run(
      { type: "first-page", items: [photo("c", 3), photo("a", 1)], nextCursor: null },
      { type: "created", photo: photo("b", 2) },
    );
    expect(ids(state)).toEqual(["c", "b", "a"]);
  });
});

describe("feedReducer de-duplication", () => {
  it("keeps one entry per id within a page", () => {
    const state = run({ type: "first-page", items: [photo("a", 1), photo("a", 1), photo("b", 2)], nextCursor: null });
    expect(ids(state)).toEqual(["b", "a"]);
  });

  it("does not duplicate a live photo that a page also contains", () => {
    const state = run(
      { type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null },
      { type: "created", photo: photo("b", 2) },
    );
    expect(ids(state)).toEqual(["b", "a"]);
  });

  it("does not duplicate photos shared by the first page and an older page", () => {
    const state = run(
      { type: "first-page", items: [photo("d", 4), photo("c", 3)], nextCursor: "c1" },
      { type: "older-page", items: [photo("c", 3), photo("b", 2), photo("a", 1)], nextCursor: null },
    );
    expect(ids(state)).toEqual(["d", "c", "b", "a"]);
  });

  it("replaces a known photo with the incoming copy", () => {
    const state = run(
      { type: "first-page", items: [photo("a", 1)], nextCursor: null },
      { type: "created", photo: photo("a", 1, { caption: "Updated caption" }) },
    );
    expect(state.photos).toHaveLength(1);
    expect(state.photos[0]?.caption).toBe("Updated caption");
  });
});

describe("feedReducer first-page", () => {
  it("loads the first page into an empty feed", () => {
    const state = run({ type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: "cursor-1" });
    expect(ids(state)).toEqual(["b", "a"]);
    expect(state.nextCursor).toBe("cursor-1");
  });

  it("records that there is nothing older when the first page is complete", () => {
    const state = run({ type: "first-page", items: [photo("a", 1)], nextCursor: null });
    expect(state.nextCursor).toBeNull();
  });

  it("starts with no cursor loaded", () => {
    expect(initialFeed.nextCursor).toBeUndefined();
    expect(initialFeed.photos).toEqual([]);
    expect(initialFeed.deletedIds.size).toBe(0);
  });

  it("replaces everything when the page is complete (photos deleted while offline disappear)", () => {
    const state = run(
      { type: "first-page", items: [photo("c", 3), photo("b", 2), photo("a", 1)], nextCursor: null },
      { type: "first-page", items: [photo("d", 4), photo("b", 2)], nextCursor: null },
    );
    expect(ids(state)).toEqual(["d", "b"]);
    expect(state.nextCursor).toBeNull();
  });

  it("an empty complete page empties the feed", () => {
    const state = run(
      { type: "first-page", items: [photo("a", 1)], nextCursor: null },
      { type: "first-page", items: [], nextCursor: null },
    );
    expect(state.photos).toEqual([]);
  });

  it("a partial page keeps only local photos older than everything it covers", () => {
    const state = run(
      { type: "first-page", items: [photo("p8", 8), photo("p5", 5)], nextCursor: "c1" },
      { type: "older-page", items: [photo("p2", 2), photo("p1", 1)], nextCursor: null },
      // p8 and p5 fall inside the range this page covers but are missing: they were deleted.
      { type: "first-page", items: [photo("p9", 9), photo("p7", 7), photo("p4", 4)], nextCursor: "c2" },
    );
    expect(ids(state)).toEqual(["p9", "p7", "p4", "p2", "p1"]);
  });

  it("a partial page keeps an existing older-pages cursor", () => {
    const state = run(
      { type: "first-page", items: [photo("f", 6), photo("e", 5)], nextCursor: "page-2" },
      { type: "older-page", items: [photo("d", 4), photo("c", 3)], nextCursor: "page-3" },
      // A resync after a reconnect, overlapping what is already loaded.
      { type: "first-page", items: [photo("g", 7), photo("f", 6)], nextCursor: "fresh-page-2" },
    );
    expect(ids(state)).toEqual(["g", "f", "e", "d", "c"]);
    expect(state.nextCursor).toBe("page-3");
  });

  it("a complete page clears an existing older-pages cursor", () => {
    const state = run(
      { type: "first-page", items: [photo("b", 2)], nextCursor: "page-2" },
      { type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null },
    );
    expect(state.nextCursor).toBeNull();
  });

  it("does not add photos that were deleted over the socket", () => {
    const state = run(
      { type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null },
      { type: "deleted", id: "b" },
      { type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null },
    );
    expect(ids(state)).toEqual(["a"]);
  });

  it("does not change the deleted ids", () => {
    const before = run({ type: "deleted", id: "x" });
    const after = feedReducer(before, { type: "first-page", items: [photo("a", 1)], nextCursor: null });
    expect(after.deletedIds).toBe(before.deletedIds);
  });

  // BUG (feed.ts first-page): the kept older photos and the kept cursor assume the
  // new page overlaps what is already loaded. When more photos than one page arrived
  // while the device was offline or asleep, the photos between the page and the old
  // local ones become unreachable: hasMore is false (or the old cursor skips them).
  it("a page that does not reach the local photos still lets the gap be loaded (complete list before)", () => {
    const state = run(
      { type: "first-page", items: [photo("old2", 2), photo("old1", 1)], nextCursor: null },
      // Three photos were uploaded meanwhile (minutes 3..5); the page holds the newest two.
      { type: "first-page", items: [photo("new5", 5), photo("new4", 4)], nextCursor: "after-new4" },
    );
    // "new3" can only be fetched through the page's own cursor.
    expect(state.nextCursor).toBe("after-new4");
  });

  it("a page that does not reach the local photos still lets the gap be loaded (older pages loaded)", () => {
    const state = run(
      { type: "first-page", items: [photo("old4", 4), photo("old3", 3)], nextCursor: "after-old3" },
      { type: "older-page", items: [photo("old2", 2)], nextCursor: "after-old2" },
      // Photos at minutes 5..9 were uploaded meanwhile; the page holds the newest two.
      { type: "first-page", items: [photo("new9", 9), photo("new8", 8)], nextCursor: "after-new8" },
    );
    expect(state.nextCursor).toBe("after-new8");
  });
});

describe("feedReducer older-page", () => {
  it("appends older photos and advances the cursor", () => {
    const state = run(
      { type: "first-page", items: [photo("d", 4), photo("c", 3)], nextCursor: "c1" },
      { type: "older-page", items: [photo("b", 2)], nextCursor: "c2" },
    );
    expect(ids(state)).toEqual(["d", "c", "b"]);
    expect(state.nextCursor).toBe("c2");
  });

  it("records the end of the list", () => {
    const state = run(
      { type: "first-page", items: [photo("b", 2)], nextCursor: "c1" },
      { type: "older-page", items: [photo("a", 1)], nextCursor: null },
    );
    expect(state.nextCursor).toBeNull();
  });

  it("keeps newer local photos (older pages never drop anything)", () => {
    const state = run(
      { type: "first-page", items: [photo("c", 3)], nextCursor: "c1" },
      { type: "created", photo: photo("z", 30) },
      { type: "older-page", items: [photo("a", 1)], nextCursor: null },
    );
    expect(ids(state)).toEqual(["z", "c", "a"]);
  });

  it("does not re-add deleted photos from a stale page", () => {
    const state = run(
      { type: "first-page", items: [photo("c", 3)], nextCursor: "c1" },
      { type: "deleted", id: "b" },
      { type: "older-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null },
    );
    expect(ids(state)).toEqual(["c", "a"]);
  });
});

describe("feedReducer created / deleted", () => {
  it("adds a live photo", () => {
    const state = run({ type: "created", photo: photo("a", 1) });
    expect(ids(state)).toEqual(["a"]);
  });

  it("removes a deleted photo and remembers its id", () => {
    const state = run(
      { type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null },
      { type: "deleted", id: "b" },
    );
    expect(ids(state)).toEqual(["a"]);
    expect(state.deletedIds.has("b")).toBe(true);
  });

  it("remembers deletions of photos it never had", () => {
    const state = run({ type: "deleted", id: "ghost" }, { type: "created", photo: photo("ghost", 1) });
    expect(state.photos).toEqual([]);
  });

  it("ignores a late 'created' for a deleted photo and returns the same state", () => {
    const deleted = run({ type: "first-page", items: [photo("a", 1)], nextCursor: null }, { type: "deleted", id: "b" });
    const after = feedReducer(deleted, { type: "created", photo: photo("b", 2) });
    expect(after).toBe(deleted);
  });

  it("does not mutate the previous state", () => {
    const before = run({ type: "first-page", items: [photo("b", 2), photo("a", 1)], nextCursor: null });
    const beforePhotos = [...before.photos];
    const after = feedReducer(before, { type: "deleted", id: "a" });
    expect(before.photos).toEqual(beforePhotos);
    expect(before.deletedIds.has("a")).toBe(false);
    expect(after.deletedIds).not.toBe(before.deletedIds);
  });

  it("remembers at most 500 deletions, forgetting the oldest first", () => {
    let state = initialFeed;
    for (let index = 0; index < 501; index += 1) {
      state = feedReducer(state, { type: "deleted", id: `gone-${index}` });
    }
    expect(state.deletedIds.size).toBe(500);
    expect(state.deletedIds.has("gone-0")).toBe(false);
    expect(state.deletedIds.has("gone-1")).toBe(true);
    expect(state.deletedIds.has("gone-500")).toBe(true);

    // The forgotten id can come back; a remembered one cannot.
    state = feedReducer(state, { type: "created", photo: photo("gone-0", 1) });
    state = feedReducer(state, { type: "created", photo: photo("gone-500", 2) });
    expect(ids(state)).toEqual(["gone-0"]);
  });

  it("does not grow when the same id is deleted twice", () => {
    const state = run({ type: "deleted", id: "a" }, { type: "deleted", id: "a" });
    expect(state.deletedIds.size).toBe(1);
  });
});

describe("feedReducer reset", () => {
  it("returns the initial state, forgetting photos, cursor and deletions", () => {
    const state = run(
      { type: "first-page", items: [photo("a", 1)], nextCursor: "c1" },
      { type: "deleted", id: "b" },
      { type: "reset" },
    );
    expect(state).toBe(initialFeed);
    expect(state.photos).toEqual([]);
    expect(state.nextCursor).toBeUndefined();
    expect(state.deletedIds.size).toBe(0);
  });
});
