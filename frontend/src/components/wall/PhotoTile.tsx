import { memo } from "react";

import { mediaUrl } from "../../lib/env";
import type { Photo } from "../../lib/types";
import { cn } from "../ui/cn";
import { describePhoto } from "./photoText";

/** Longest edge of the server's thumbnails (GK_THUMBNAIL_SIZE). */
const THUMBNAIL_EDGE = 640;

interface PhotoTileProps {
  photo: Photo;
  /** Just arrived live: pop in with a brief brand ring. */
  highlight: boolean;
  onOpen: (photoId: string) => void;
  /** Photos per row in the grid. */
  columns: number;
}

/**
 * Big tiles (one or two per row) may use the full photo when the thumbnail would look soft;
 * the browser picks. Smaller tiles always use the thumbnail, sparing the server's upload speed.
 */
function sources(photo: Photo, columns: number): { srcSet?: string; sizes?: string } {
  const full = mediaUrl(photo.url);
  if (columns > 2 || !full) return {};
  const thumbWidth = Math.round(Math.min(photo.width, (THUMBNAIL_EDGE * photo.width) / Math.max(photo.width, photo.height)));
  return {
    srcSet: `${mediaUrl(photo.thumbnail_url)} ${thumbWidth}w, ${full} ${photo.width}w`,
    sizes: `${Math.ceil(100 / columns)}vw`,
  };
}

function PhotoTileView({ photo, highlight, onOpen, columns }: PhotoTileProps) {
  const name = photo.uploader_name?.trim();
  const caption = photo.caption?.trim();
  // One per row shows the whole photo, uncropped; otherwise square tiles.
  const whole = columns === 1;

  return (
    <li className={cn(highlight && "animate-pop-in")}>
      <button
        type="button"
        data-photo-id={photo.id}
        aria-haspopup="dialog"
        onClick={() => onOpen(photo.id)}
        className={cn(
          "@container group relative block w-full overflow-hidden bg-stone-900 transition-shadow duration-700",
          columns >= 6 ? "rounded-md" : "rounded-xl",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400",
          highlight ? "shadow-lg shadow-brand-500/30 ring-4 ring-brand-500" : "ring-1 ring-white/5",
        )}
      >
        <img
          src={mediaUrl(photo.thumbnail_url)}
          {...sources(photo, columns)}
          alt={describePhoto(photo)}
          width={photo.width}
          height={photo.height}
          loading="lazy"
          decoding="async"
          style={whole ? { aspectRatio: `${photo.width} / ${photo.height}` } : undefined}
          className={cn(
            "block w-full object-cover transition-transform duration-500 ease-out group-hover:scale-[1.04]",
            !whole && "aspect-square",
          )}
        />
        {(name || caption) && (
          // Visual only: the image's alt text already says the same. Hidden on small tiles, where it would cover the photo.
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 bottom-0 bg-linear-to-t from-black/80 via-black/40 to-transparent px-3 pb-2.5 pt-10 text-left @max-[9rem]:hidden 2xl:px-4 2xl:pb-4"
          >
            {caption && <span className="line-clamp-2 text-sm font-semibold leading-snug text-white 2xl:text-lg">{caption}</span>}
            {name && <span className="block truncate text-xs font-medium text-white/75 2xl:text-base">{name}</span>}
          </span>
        )}
      </button>
    </li>
  );
}

// Photos never change after upload, so a refreshed copy of the same photo
// (every reconnect re-fetches the first page) doesn't need a re-render.
export const PhotoTile = memo(
  PhotoTileView,
  (previous, next) =>
    previous.photo.id === next.photo.id &&
    previous.highlight === next.highlight &&
    previous.onOpen === next.onOpen &&
    previous.columns === next.columns,
);
