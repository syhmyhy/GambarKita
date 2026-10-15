import { timeAgo } from "../../lib/format";
import type { Photo } from "../../lib/types";
import { cn } from "../ui/cn";

/** Caption, uploader and time for a photo shown on a dark background. */
export function PhotoCaption({ photo, large = false, className }: { photo: Photo; large?: boolean; className?: string }) {
  const name = photo.uploader_name?.trim() || "A guest";
  const caption = photo.caption?.trim();
  const created = new Date(photo.created_at);

  return (
    <div className={cn("min-w-0", className)}>
      {caption && (
        <p
          className={cn(
            "line-clamp-3 font-bold tracking-tight text-pretty text-white",
            large ? "text-xl sm:text-3xl 2xl:text-4xl" : "text-base sm:text-lg",
          )}
        >
          {caption}
        </p>
      )}
      <p className={cn("flex flex-wrap items-center gap-x-2 text-white/75", caption && "mt-1", large ? "text-base sm:text-xl" : "text-sm")}>
        <span className="font-semibold text-white/90">{name}</span>
        <span aria-hidden="true">·</span>
        <time dateTime={photo.created_at} title={Number.isNaN(created.getTime()) ? undefined : created.toLocaleString()}>
          {timeAgo(photo.created_at)}
        </time>
      </p>
    </div>
  );
}
