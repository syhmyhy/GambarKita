import { ChevronLeft, ChevronRight, Pause, Sparkles } from "lucide-react";
import { memo, useEffect, useEffectEvent, useMemo, useReducer, useRef, useState } from "react";

import { mediaUrl } from "../../lib/env";
import type { Photo } from "../../lib/types";
import { IconButton } from "../ui/Button";
import { cn } from "../ui/cn";
import { PhotoCaption } from "./PhotoCaption";
import { describePhoto } from "./photoText";
import { currentSlideId, initialSlideshow, slideshowReducer } from "./slideshowState";
import { usePreloadImages } from "./usePageChrome";
import { useSlideTimer } from "./useSlideTimer";
import { useSwipe } from "./useSwipe";

export const SLIDE_DURATION_MS = 7000;

interface SlideshowProps {
  /** Newest first. */
  photos: Photo[];
  /** The event's photo count (more than ``photos`` while older pages aren't loaded). */
  total: number;
  latestLiveId: string | null;
  paused: boolean;
  onTogglePause: () => void;
  /** Hide the on-screen arrows while nobody is interacting. */
  controlsHidden: boolean;
  /** Called when the rotation nears the oldest loaded photo, to fetch more. */
  onNearEnd?: () => void;
}

const INTERACTIVE = "button, a, input, textarea, select, [contenteditable='true'], [role='button']";

/** One photo at a time, full screen, cross-fading every few seconds. */
export function Slideshow({ photos, total, latestLiveId, paused, onTogglePause, controlsHidden, onNearEnd }: SlideshowProps) {
  const ids = useMemo(() => photos.map((photo) => photo.id), [photos]);
  const byId = useMemo(() => new Map(photos.map((photo) => [photo.id, photo])), [photos]);
  const [state, dispatch] = useReducer(slideshowReducer, ids, initialSlideshow);
  const regionRef = useRef<HTMLElement>(null);
  const barRef = useRef<HTMLDivElement>(null);

  // New live photos jump the queue (adjusting state while rendering, not in an effect).
  const [seenLiveId, setSeenLiveId] = useState(latestLiveId);
  if (latestLiveId !== seenLiveId) {
    setSeenLiveId(latestLiveId);
    if (latestLiveId !== null) dispatch({ type: "enqueue", id: latestLiveId });
  }

  const next = () => dispatch({ type: "next", ids });
  const prev = () => dispatch({ type: "prev", ids });
  useSlideTimer({ slideKey: state.step, duration: SLIDE_DURATION_MS, paused, onElapsed: next, barRef });
  const swipe = useSwipe(next, prev);

  const currentId = currentSlideId(state, ids);
  const current = currentId !== null ? byId.get(currentId) : undefined;
  const previous = state.previousId !== null && state.previousId !== currentId ? byId.get(state.previousId) : undefined;
  const upcoming = useMemo(() => {
    const id = slideshowReducer(state, { type: "next", ids }).currentId;
    return id !== null ? byId.get(id) : undefined;
  }, [state, ids, byId]);
  usePreloadImages(upcoming ? [mediaUrl(upcoming.url), mediaUrl(upcoming.thumbnail_url)] : []);

  const index = currentId !== null ? ids.indexOf(currentId) : -1;
  const nearEnd = index >= ids.length - 3;
  useEffect(() => {
    if (nearEnd) onNearEnd?.();
  }, [nearEnd, onNearEnd]);

  // Take focus from the mode toggle, so Space pauses instead of switching back.
  useEffect(() => {
    regionRef.current?.focus({ preventScroll: true });
  }, []);

  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
    if (event.key === "ArrowRight") next();
    else if (event.key === "ArrowLeft") prev();
    // Space on a focused button presses that button instead.
    else if (event.key === " " && !target?.closest(INTERACTIVE)) onTogglePause();
    else return;
    event.preventDefault();
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKeyDown(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  if (!current) return null;

  const controlClass = cn(
    "absolute top-1/2 size-12 -translate-y-1/2 transition-opacity duration-500",
    controlsHidden && "pointer-events-none opacity-0 focus-visible:pointer-events-auto focus-visible:opacity-100",
  );

  return (
    <section
      ref={regionRef}
      tabIndex={-1}
      aria-roledescription="carousel"
      aria-label="Photo slideshow"
      className="relative h-dvh w-full overflow-hidden bg-black focus:outline-none"
      {...swipe}
    >
      {/* Announce slides only while paused; a rotating announcement would be noise. */}
      <div aria-live={paused ? "polite" : "off"} className="absolute inset-0">
        {previous && <Slide key={previous.id} photo={previous} />}
        <Slide
          key={current.id}
          photo={current}
          label={`Photo ${(index + 1).toLocaleString()} of ${Math.max(total, ids.length).toLocaleString()}`}
        />
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 bg-linear-to-t from-black/85 via-black/40 to-transparent pb-[max(1.75rem,env(safe-area-inset-bottom))] pl-[max(1.5rem,env(safe-area-inset-left))] pr-[max(1.5rem,env(safe-area-inset-right))] pt-28 sm:pl-10">
        <div className="max-w-2xl">
          {(state.fresh || paused) && (
            <div className="mb-3 flex flex-wrap gap-2">
              {state.fresh && (
                <span key={current.id} className="inline-flex animate-pop-in items-center gap-1.5 rounded-full bg-brand-600 px-3.5 py-1 text-sm font-bold text-white shadow-lg sm:text-base">
                  <Sparkles className="size-4" aria-hidden="true" />
                  New photo
                </span>
              )}
              {paused && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3.5 py-1 text-sm font-semibold text-white ring-1 ring-white/20 backdrop-blur sm:text-base">
                  <Pause className="size-4" aria-hidden="true" />
                  Paused
                </span>
              )}
            </div>
          )}
          <PhotoCaption key={current.id} photo={current} large className="animate-fade-in" />
        </div>
      </div>

      {ids.length > 1 && (
        <>
          <IconButton variant="light" label="Previous photo" className={cn(controlClass, "left-[max(0.75rem,env(safe-area-inset-left))] z-20")} onClick={prev}>
            <ChevronLeft className="size-6" aria-hidden="true" />
          </IconButton>
          <IconButton variant="light" label="Next photo" className={cn(controlClass, "right-[max(0.75rem,env(safe-area-inset-right))] z-20")} onClick={next}>
            <ChevronRight className="size-6" aria-hidden="true" />
          </IconButton>
        </>
      )}

      <div className="absolute inset-x-0 bottom-0 z-30 h-1 bg-white/10 motion-reduce:hidden" aria-hidden="true">
        <div ref={barRef} className="h-full origin-left bg-brand-500" style={{ transform: "scaleX(0)" }} />
      </div>
    </section>
  );
}

/**
 * A full-screen photo over a blurred copy of itself. The newest slide sits on
 * top and fades in over the previous one, which gives the cross-fade.
 */
const Slide = memo(function Slide({ photo, label }: { photo: Photo; label?: string }) {
  const isCurrent = label !== undefined;
  return (
    <div
      role={isCurrent ? "group" : undefined}
      aria-roledescription={isCurrent ? "slide" : undefined}
      aria-label={label}
      aria-hidden={isCurrent ? undefined : true}
      className={cn("absolute inset-0 overflow-hidden", isCurrent && "z-10 animate-fade-in")}
      style={isCurrent ? { animationDuration: "1.2s" } : undefined}
    >
      <img
        src={mediaUrl(photo.thumbnail_url)}
        alt=""
        aria-hidden="true"
        className="absolute inset-0 size-full scale-110 object-cover opacity-60 blur-2xl"
      />
      <img
        src={mediaUrl(photo.url)}
        alt={describePhoto(photo)}
        decoding="async"
        className="relative size-full animate-ken-burns object-contain"
      />
    </div>
  );
});
