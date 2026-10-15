import { ImageOff, Trash2 } from "lucide-react";

import { IconButton } from "../ui/Button";
import { cn } from "../ui/cn";
import { mediaUrl } from "../../lib/env";
import { timeAgo } from "../../lib/format";
import type { Photo } from "../../lib/types";

export function uploaderLabel(photo: Photo): string {
  return photo.uploader_name?.trim() || "a guest";
}

function altText(photo: Photo): string {
  const by = `Photo by ${uploaderLabel(photo)}`;
  return photo.caption ? `${by}: ${photo.caption}` : by;
}

/** Tells apart the delete buttons of photos by the same guest: the caption, or else when it arrived. */
function deleteLabel(photo: Photo): string {
  const by = `Delete photo by ${uploaderLabel(photo)}`;
  return photo.caption ? `${by}: ${photo.caption}` : `${by}, ${timeAgo(photo.created_at)}`;
}

export function PhotoTile({ photo, isNew, onDelete }: { photo: Photo; isNew: boolean; onDelete: (photo: Photo) => void }) {
  const thumbnail = mediaUrl(photo.thumbnail_url);
  const full = mediaUrl(photo.url);

  const image = thumbnail ? (
    <img src={thumbnail} alt={altText(photo)} loading="lazy" decoding="async" className="size-full object-cover" />
  ) : (
    <span className="grid size-full place-items-center text-stone-400" role="img" aria-label={altText(photo)}>
      <ImageOff className="size-8" aria-hidden="true" />
    </span>
  );

  return (
    <li className={cn("group min-w-0", isNew && "animate-pop-in")}>
      <figure>
        <div className="relative aspect-square overflow-hidden rounded-2xl bg-stone-200 ring-1 ring-stone-200/80 dark:bg-stone-800 dark:ring-stone-800">
          {full ? (
            <a
              href={full}
              target="_blank"
              rel="noreferrer"
              className="block size-full rounded-2xl focus-visible:outline-offset-[-3px]"
              title="Open full size in a new tab"
            >
              {image}
            </a>
          ) : (
            image
          )}
          <IconButton
            label={deleteLabel(photo)}
            variant="danger"
            onClick={() => onDelete(photo)}
            className={cn(
              "absolute right-2 top-2 shadow-md",
              // Revealed on hover/focus with a mouse; always visible on touch screens.
              "pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100",
            )}
          >
            <Trash2 className="size-4" aria-hidden="true" />
          </IconButton>
        </div>
        <figcaption className="mt-2 px-0.5 text-sm">
          <span className="block truncate font-semibold">{photo.uploader_name?.trim() || "Guest"}</span>
          {photo.caption && <span className="line-clamp-2 text-stone-600 dark:text-stone-400">{photo.caption}</span>}
          <time dateTime={photo.created_at} className="block text-xs text-stone-500 dark:text-stone-400">
            {timeAgo(photo.created_at)}
          </time>
        </figcaption>
      </figure>
    </li>
  );
}
