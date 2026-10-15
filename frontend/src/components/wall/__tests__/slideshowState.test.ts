import { describe, expect, it } from "vitest";

import { currentSlideId, initialSlideshow, type SlideshowState, slideshowReducer } from "../slideshowState";

const next = (state: SlideshowState, ids: string[]) => slideshowReducer(state, { type: "next", ids });
const prev = (state: SlideshowState, ids: string[]) => slideshowReducer(state, { type: "prev", ids });

describe("slideshowReducer", () => {
  it("starts at the newest photo and loops through the rest", () => {
    const ids = ["c", "b", "a"];
    let state = initialSlideshow(ids);
    expect(state.currentId).toBe("c");
    state = next(state, ids);
    expect(state.currentId).toBe("b");
    expect(state.previousId).toBe("c");
    state = next(next(state, ids), ids);
    expect(state.currentId).toBe("c");
    expect(prev(state, ids).currentId).toBe("a");
  });

  it("shows live photos next, then resumes the rotation where it left off", () => {
    let ids = ["c", "b", "a"];
    let state = next(initialSlideshow(ids), ids); // on "b"
    ids = ["e", "d", ...ids];
    state = slideshowReducer(state, { type: "enqueue", id: "d" });
    state = slideshowReducer(state, { type: "enqueue", id: "e" });

    state = next(state, ids);
    expect(state).toMatchObject({ currentId: "d", fresh: true, resumeId: "b" });
    state = next(state, ids);
    expect(state).toMatchObject({ currentId: "e", fresh: true, resumeId: "b" });
    state = next(state, ids);
    expect(state).toMatchObject({ currentId: "a", fresh: false, resumeId: null, queue: [] });
  });

  it("goes back to the interrupted photo on prev from a live photo", () => {
    let ids = ["b", "a"];
    let state = next(initialSlideshow(ids), ids); // on "a"
    ids = ["c", ...ids];
    state = next(slideshowReducer(state, { type: "enqueue", id: "c" }), ids);
    expect(state.currentId).toBe("c");
    expect(prev(state, ids)).toMatchObject({ currentId: "a", fresh: false });
  });

  it("skips queued photos that were deleted and ignores duplicates", () => {
    let state = initialSlideshow(["b", "a"]);
    state = slideshowReducer(state, { type: "enqueue", id: "x" });
    state = slideshowReducer(state, { type: "enqueue", id: "x" });
    expect(state.queue).toEqual(["x"]);
    expect(next(state, ["b", "a"])).toMatchObject({ currentId: "a", queue: [] });
  });

  it("falls back to the newest photo when the current one is deleted", () => {
    const state = next(initialSlideshow(["c", "b", "a"]), ["c", "b", "a"]);
    expect(currentSlideId(state, ["c", "a"])).toBe("c");
  });

  it("still counts a step with a single photo so the timer restarts", () => {
    const state = initialSlideshow(["a"]);
    const moved = next(state, ["a"]);
    expect(moved.currentId).toBe("a");
    expect(moved.previousId).toBeNull();
    expect(moved.step).toBe(state.step + 1);
  });
});
