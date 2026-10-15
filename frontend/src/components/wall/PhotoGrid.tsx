import { History } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Photo } from "../../lib/types";
import { Button } from "../ui/Button";
import { cn } from "../ui/cn";
import { PhotoTile } from "./PhotoTile";
import { type GridZoom, usePinchZoom } from "./useGridZoom";

const HIGHLIGHT_MS = 4000;

/** Tighter gaps as the tiles get smaller, like iPhone Photos. */
function gapFor(columns: number): string {
  if (columns >= 6) return "gap-1";
  if (columns >= 4) return "gap-2 sm:gap-3";
  return "gap-3";
}

interface PhotoGridProps {
  photos: Photo[];
  latestLiveId: string | null;
  onOpen: (photoId: string) => void;
  hasMore: boolean;
  loadingMore: boolean;
  /** The last page load failed: stop loading automatically, keep the button. */
  loadFailed: boolean;
  onLoadMore: () => void;
  /** Leave room at the bottom for the QR panel on large screens. */
  reserveCorner: boolean;
  /** How many photos per row; pinching the grid calls ``onZoom``. */
  columns: number;
  onZoom: GridZoom["zoom"];
}

/**
 * Square thumbnails in rows, newest first (top left), with infinite scroll for
 * older photos. Row-major order keeps every tile in place when a new photo
 * arrives or an older page loads, so nothing reshuffles under the viewer.
 */
export function PhotoGrid({
  photos,
  latestLiveId,
  onOpen,
  hasMore,
  loadingMore,
  loadFailed,
  onLoadMore,
  reserveCorner,
  columns,
  onZoom,
}: PhotoGridProps) {
  const sectionRef = useRef<HTMLElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const highlightId = useBriefly(latestLiveId, HIGHLIGHT_MS);
  usePinchZoom(sectionRef, onZoom);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || !hasMore || loadingMore || loadFailed || typeof IntersectionObserver === "undefined") return;
    // Re-created after every load, so a sentinel that is still visible triggers the next page.
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) onLoadMore();
      },
      { rootMargin: "0px 0px 1200px 0px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMore, loadingMore, loadFailed, onLoadMore]);

  return (
    <section
      ref={sectionRef}
      aria-label="Photos"
      className={cn(
        // Clear the notch / home indicator side insets on iPhones held in landscape.
        "pb-24 pl-[max(0.75rem,env(safe-area-inset-left))] pr-[max(0.75rem,env(safe-area-inset-right))] pt-3",
        "sm:pl-[max(1rem,env(safe-area-inset-left))] sm:pr-[max(1rem,env(safe-area-inset-right))]",
        reserveCorner && "lg:pb-80",
      )}
    >
      <ul
        role="list"
        className={cn("grid", gapFor(columns))}
        style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
      >
        {photos.map((photo) => (
          <PhotoTile key={photo.id} photo={photo} highlight={photo.id === highlightId} onOpen={onOpen} columns={columns} />
        ))}
      </ul>
      <div ref={sentinelRef} aria-hidden="true" className="h-px" />
      {hasMore && (
        <div className="mt-6 flex justify-center">
          <Button variant="light" loading={loadingMore} onClick={onLoadMore} icon={<History className="size-4" aria-hidden="true" />}>
            Load older photos
          </Button>
        </div>
      )}
      <p role="status" className="sr-only">
        {loadingMore ? "Loading older photos…" : ""}
      </p>
    </section>
  );
}

/** ``value`` for ``duration`` ms after it last changed (null before and after). */
function useBriefly(value: string | null, duration: number): string | null {
  // Whatever is current on mount counts as already seen (e.g. switching back from the slideshow).
  const [expired, setExpired] = useState(value);
  useEffect(() => {
    if (value === null) return;
    const timer = window.setTimeout(() => setExpired(value), duration);
    return () => window.clearTimeout(timer);
  }, [value, duration]);
  return value !== expired ? value : null;
}
