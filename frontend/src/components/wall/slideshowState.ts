/**
 * Pure state for the slideshow. The rotation walks the photos newest first and
 * loops; photos that arrive live jump the queue and are shown next, after
 * which the rotation continues where it left off.
 */
export interface SlideshowState {
  currentId: string | null;
  /** The photo being faded out underneath the current one. */
  previousId: string | null;
  /** Where the rotation continues once the queued live photos have been shown. */
  resumeId: string | null;
  /** Live photos waiting to be shown next, in arrival order. */
  queue: string[];
  /** The current photo came from the queue (it gets a "New photo" pill). */
  fresh: boolean;
  /** Increases on every move, even onto the same photo, so the timer restarts. */
  step: number;
}

export type SlideshowAction =
  | { type: "next"; ids: readonly string[] }
  | { type: "prev"; ids: readonly string[] }
  | { type: "enqueue"; id: string };

export function initialSlideshow(ids: readonly string[]): SlideshowState {
  return { currentId: ids[0] ?? null, previousId: null, resumeId: null, queue: [], fresh: false, step: 0 };
}

/** The photo on screen: the stored one while it still exists, else the newest. */
export function currentSlideId(state: SlideshowState, ids: readonly string[]): string | null {
  return state.currentId !== null && ids.includes(state.currentId) ? state.currentId : (ids[0] ?? null);
}

function moveTo(state: SlideshowState, from: string, to: string, patch: Partial<SlideshowState>): SlideshowState {
  return {
    ...state,
    ...patch,
    currentId: to,
    previousId: to === from ? state.previousId : from,
    step: state.step + 1,
  };
}

function neighbour(ids: readonly string[], id: string, offset: 1 | -1): string {
  const index = ids.indexOf(id);
  return ids[(index + offset + ids.length) % ids.length] ?? id;
}

export function slideshowReducer(state: SlideshowState, action: SlideshowAction): SlideshowState {
  if (action.type === "enqueue") {
    if (state.queue.includes(action.id) || state.currentId === action.id) return state;
    return { ...state, queue: [...state.queue, action.id] };
  }

  const { ids } = action;
  const current = currentSlideId(state, ids);
  if (current === null) return state;
  const present = new Set(ids);
  const resume = state.fresh && state.resumeId !== null && present.has(state.resumeId) ? state.resumeId : null;

  if (action.type === "prev") {
    return moveTo(state, current, resume ?? neighbour(ids, current, -1), { fresh: false, resumeId: null });
  }

  const queue = state.queue.filter((id) => present.has(id) && id !== current);
  const [queued, ...rest] = queue;
  if (queued !== undefined) {
    return moveTo(state, current, queued, { queue: rest, fresh: true, resumeId: resume ?? current });
  }
  return moveTo(state, current, neighbour(ids, resume ?? current, 1), { queue, fresh: false, resumeId: null });
}
