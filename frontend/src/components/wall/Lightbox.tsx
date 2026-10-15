import { ChevronLeft, ChevronRight, Download, RefreshCw, X } from "lucide-react";
import { type KeyboardEvent, useEffect, useEffectEvent, useId, useRef, useState } from "react";

import { mediaUrl } from "../../lib/env";
import type { Photo } from "../../lib/types";
import { Button, buttonClasses, IconButton } from "../ui/Button";
import { cn } from "../ui/cn";
import { Spinner } from "../ui/Spinner";
import { PhotoCaption } from "./PhotoCaption";
import { describePhoto } from "./photoText";
import { usePreloadImages } from "./usePageChrome";
import { useSwipe } from "./useSwipe";

interface LightboxProps {
  /** Newest first, as on the grid. */
  photos: Photo[];
  photoId: string;
  /** The event's photo count (more than ``photos`` while older pages aren't loaded). */
  total: number;
  onNavigate: (photoId: string) => void;
  onClose: () => void;
  /** Called when the viewer gets close to the oldest loaded photo. */
  onNearEnd?: () => void;
  /**
   * Loading older photos failed. Toasts sit underneath the modal dialog, so the
   * error (and a retry) is shown in here once the viewer reaches the oldest photo.
   */
  loadFailed?: boolean;
  loadingMore?: boolean;
  onRetry?: () => void;
}

function findTile(photoId: string): HTMLElement | null {
  for (const element of document.querySelectorAll<HTMLElement>("[data-photo-id]")) {
    if (element.dataset.photoId === photoId) return element;
  }
  return null;
}

/**
 * Full-screen photo viewer on a native modal <dialog> (focus trap and inert
 * background come from the browser). On close, focus returns to the grid tile
 * of the photo that was last shown.
 */
export function Lightbox({
  photos,
  photoId,
  total,
  onNavigate,
  onClose,
  onNearEnd,
  loadFailed = false,
  loadingMore = false,
  onRetry,
}: LightboxProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const index = photos.findIndex((photo) => photo.id === photoId);
  const photo = photos[index];
  const newer = index > 0 ? photos[index - 1] : undefined;
  const older = index >= 0 ? photos[index + 1] : undefined;

  const showNewer = () => newer && onNavigate(newer.id);
  const showOlder = () => older && onNavigate(older.id);
  const swipe = useSwipe(showOlder, showNewer);
  usePreloadImages([older ? mediaUrl(older.url) : "", newer ? mediaUrl(newer.url) : ""]);

  const restoreFocus = useEffectEvent((opener: HTMLElement | null) => {
    (findTile(photoId) ?? opener)?.focus();
  });

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    dialog.querySelector<HTMLElement>("[data-initial-focus]")?.focus();
    return () => {
      if (dialog.open) dialog.close();
      restoreFocus(opener);
    };
  }, []);

  const nearEnd = index >= photos.length - 3;
  useEffect(() => {
    if (nearEnd) onNearEnd?.();
  }, [nearEnd, onNearEnd]);

  if (!photo) return null;

  const onKeyDown = (event: KeyboardEvent<HTMLDialogElement>) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowLeft") showNewer();
    else if (event.key === "ArrowRight") showOlder();
    else if (event.key === "Escape") onClose();
    else return;
    event.preventDefault();
  };

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      aria-modal="true"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={onKeyDown}
      {...swipe}
      className="fixed inset-0 m-0 size-full max-h-none max-w-none overflow-hidden border-0 bg-black/95 p-0 text-white backdrop:bg-black/70"
    >
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] pt-[max(0.75rem,env(safe-area-inset-top))]">
          <h2 id={titleId} className="min-w-0 flex-1 truncate text-sm font-semibold tabular-nums text-white/80">
            Photo {(index + 1).toLocaleString()} of {Math.max(total, photos.length).toLocaleString()}
          </h2>
          <a
            href={mediaUrl(photo.url)}
            download
            target="_blank"
            rel="noreferrer"
            className={buttonClasses("light", "md")}
          >
            <Download className="size-4" aria-hidden="true" />
            Save
          </a>
          <IconButton variant="light" label="Close" onClick={onClose} data-initial-focus="">
            <X className="size-5" aria-hidden="true" />
          </IconButton>
        </div>

        {loadFailed && !older && onRetry && (
          <div
            role="alert"
            className="mx-4 mt-3 flex flex-wrap items-center justify-center gap-x-3 gap-y-2 self-center rounded-2xl bg-white/10 py-2 pl-4 pr-2 text-sm text-white/90 ring-1 ring-white/15"
          >
            <span>Couldn't load older photos.</span>
            <Button variant="light" loading={loadingMore} onClick={onRetry} icon={<RefreshCw className="size-4" aria-hidden="true" />}>
              Retry
            </Button>
          </div>
        )}

        <figure className="flex min-h-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <LightboxImage key={photo.id} photo={photo} />
            {newer && (
              <IconButton
                variant="light"
                label="Newer photo"
                className="absolute left-[max(0.75rem,env(safe-area-inset-left))] top-1/2 size-12 -translate-y-1/2"
                onClick={showNewer}
              >
                <ChevronLeft className="size-6" aria-hidden="true" />
              </IconButton>
            )}
            {older && (
              <IconButton
                variant="light"
                label="Older photo"
                className="absolute right-[max(0.75rem,env(safe-area-inset-right))] top-1/2 size-12 -translate-y-1/2"
                onClick={showOlder}
              >
                <ChevronRight className="size-6" aria-hidden="true" />
              </IconButton>
            )}
          </div>
          <figcaption className="mx-auto w-full max-w-3xl px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 text-center">
            <PhotoCaption photo={photo} className="[&>p]:justify-center" />
          </figcaption>
        </figure>
      </div>
    </dialog>
  );
}

/** The cached thumbnail shows at once; the full image fades in over it when loaded. */
function LightboxImage({ photo }: { photo: Photo }) {
  const [state, setState] = useState<"loading" | "loaded" | "failed">("loading");
  return (
    <>
      <img
        src={mediaUrl(photo.thumbnail_url)}
        alt=""
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 size-full object-contain"
      />
      {state !== "failed" && (
        <img
          src={mediaUrl(photo.url)}
          alt={describePhoto(photo)}
          decoding="async"
          onLoad={() => setState("loaded")}
          onError={() => setState("failed")}
          className={cn(
            "absolute inset-0 size-full object-contain transition-opacity duration-300",
            state === "loaded" ? "opacity-100" : "opacity-0",
          )}
        />
      )}
      {state === "loading" && (
        <Spinner className="absolute bottom-4 left-1/2 size-6 -translate-x-1/2 text-white/70" label="Loading full size photo" />
      )}
    </>
  );
}
