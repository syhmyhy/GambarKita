import { History } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Photo } from "../../lib/types";
import { Button } from "../ui/Button";
import { cn } from "../ui/cn";
import { PhotoTile } from "./PhotoTile";

const HIGHLIGHT_MS = 4000;

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
}

/**
 * Square thumbnails in rows, newest first (top left), with infinite scroll for
 * older photos. Row-major order keeps every tile in place when a new photo
 * arrives or an older page loads, so nothing reshuffles under the viewer.
 */
export function PhotoGrid({ photos, latestLiveId, onOpen, hasMore, loadingMore, loadFailed, onLoadMore, reserveCorner }: PhotoGridProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const highlightId = useBriefly(latestLiveId, HIGHLIGHT_MS);

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
      aria-label="Photos"
      className={cn(
        // Clear the notch / home indicator side insets on iPhones held in landscape.
        "pb-24 pl-[max(0.75rem,env(safe-area-inset-left))] pr-[max(0.75rem,env(safe-area-inset-right))] pt-3",
        "sm:pl-[max(1rem,env(safe-area-inset-left))] sm:pr-[max(1rem,env(safe-area-inset-right))]",
        reserveCorner && "lg:pb-80",
      )}
    >
      <ul role="list" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5">
        {photos.map((photo) => (
          <PhotoTile key={photo.id} photo={photo} highlight={photo.id === highlightId} onOpen={onOpen} />
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
