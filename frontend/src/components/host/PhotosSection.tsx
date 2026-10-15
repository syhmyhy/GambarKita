import { ImagePlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "../ui/Button";
import { Spinner } from "../ui/Spinner";
import { useToast } from "../ui/Toast";
import { cn } from "../ui/cn";
import { api } from "../../lib/api";
import { pluralize } from "../../lib/format";
import type { Photo } from "../../lib/types";
import { PhotoTile, uploaderLabel } from "./PhotoTile";
import { hasErrorCode, type HostSession } from "./session";

/**
 * How long the host can undo a photo delete. Like Gmail's "Undo send", the photo is
 * hidden at once but the delete is only sent when this time is up (so the live wall
 * still shows it until then).
 */
export const UNDO_DELETE_MS = 5000;

interface PendingDelete {
  photo: Photo;
  timer: number;
}

interface PhotosSectionProps {
  session: HostSession;
  photos: Photo[];
  photoCount: number;
  initialLoading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  loadMore: () => Promise<void>;
  removePhoto: (photoId: string) => void;
  latestLiveId: string | null;
  className?: string;
}

export function PhotosSection({
  session,
  photos,
  photoCount,
  initialLoading,
  hasMore,
  loadingMore,
  loadMore,
  removePhoto,
  latestLiveId,
  className,
}: PhotosSectionProps) {
  const toast = useToast();
  const headingRef = useRef<HTMLHeadingElement>(null);
  /** Photos deleted on this screen whose delete hasn't reached the server yet. */
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const pending = useRef(new Map<string, PendingDelete>());

  const unhide = (photoId: string) =>
    setHidden((current) => {
      const next = new Set(current);
      next.delete(photoId);
      return next;
    });

  const sendDelete = async (photo: Photo) => {
    pending.current.delete(photo.id);
    try {
      await api.deletePhoto(session.eventId, photo.id, session.token);
      removePhoto(photo.id);
    } catch (error) {
      if (hasErrorCode(error, "PHOTO_NOT_FOUND")) {
        removePhoto(photo.id);
      } else {
        // It's still on the server: show it again.
        session.reportError(error);
      }
    } finally {
      unhide(photo.id);
    }
  };

  const deletePhoto = (photo: Photo) => {
    setHidden((current) => new Set(current).add(photo.id));
    // The photo's delete button is gone: continue from the section heading instead of <body>.
    headingRef.current?.focus();
    const timer = window.setTimeout(() => void sendDelete(photo), UNDO_DELETE_MS);
    pending.current.set(photo.id, { photo, timer });
    toast.withAction("Photo deleted", { label: "Undo", onClick: () => undoDelete(photo.id) }, UNDO_DELETE_MS);
  };

  const undoDelete = (photoId: string) => {
    const entry = pending.current.get(photoId);
    if (!entry) return; // Too late: already sent.
    window.clearTimeout(entry.timer);
    pending.current.delete(photoId);
    unhide(photoId);
    toast.info("Photo kept");
  };

  // Leaving the page (or the tab going to the background, where timers may not run)
  // sends the waiting deletes at once, so a delete the host didn't undo is never lost.
  const { eventId, token } = session;
  useEffect(() => {
    const deletes = pending.current;
    const sendAll = () => {
      for (const { photo, timer } of deletes.values()) {
        window.clearTimeout(timer);
        void api.deletePhoto(eventId, photo.id, token, { keepalive: true }).catch(() => undefined);
      }
      deletes.clear();
    };
    const onHidden = () => {
      if (document.visibilityState === "hidden") sendAll();
    };
    window.addEventListener("pagehide", sendAll);
    document.addEventListener("visibilitychange", onHidden);
    return () => {
      window.removeEventListener("pagehide", sendAll);
      document.removeEventListener("visibilitychange", onHidden);
      sendAll();
    };
  }, [eventId, token]);

  const shown = hidden.size === 0 ? photos : photos.filter((photo) => !hidden.has(photo.id));
  const latest = latestLiveId ? shown.find((photo) => photo.id === latestLiveId) : undefined;

  return (
    <section aria-labelledby="host-photos-heading" className={cn("min-w-0", className)}>
      <div className="flex items-baseline justify-between gap-3">
        <h2
          id="host-photos-heading"
          ref={headingRef}
          tabIndex={-1}
          className="rounded-md text-xl font-bold tracking-tight focus:outline-none"
        >
          Photos
        </h2>
        <p className="text-sm font-medium text-stone-500 dark:text-stone-400">
          {pluralize(Math.max(0, photoCount - hidden.size), "photo")}
        </p>
      </div>
      <p className="sr-only" aria-live="polite">
        {latest ? `New photo from ${uploaderLabel(latest)}` : ""}
      </p>

      {shown.length === 0 ? (
        initialLoading ? (
          <div className="mt-4 flex justify-center py-16 text-stone-500" role="status">
            <Spinner className="size-6" label="Loading photos" />
          </div>
        ) : (
          <EmptyPhotos />
        )
      ) : (
        <ul className="mt-4 grid grid-cols-2 gap-x-3 gap-y-5 sm:grid-cols-3 sm:gap-x-4 xl:grid-cols-4">
          {shown.map((photo) => (
            <PhotoTile key={photo.id} photo={photo} isNew={photo.id === latestLiveId} onDelete={deletePhoto} />
          ))}
        </ul>
      )}

      {hasMore && (
        <div className="mt-8 flex justify-center">
          <Button variant="secondary" loading={loadingMore} onClick={() => void loadMore().catch(session.reportError)}>
            Load more photos
          </Button>
        </div>
      )}
    </section>
  );
}

function EmptyPhotos() {
  return (
    <div className="mt-4 flex flex-col items-center rounded-[var(--radius-card)] border-2 border-dashed border-stone-300 px-6 py-14 text-center dark:border-stone-700">
      <span className="grid size-14 place-items-center rounded-2xl bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-400">
        <ImagePlus className="size-7" aria-hidden="true" />
      </span>
      <p className="mt-4 font-bold">No photos yet</p>
      <p className="mt-1 max-w-sm text-sm text-stone-600 dark:text-stone-400">
        Share the QR code with your guests. Their photos appear here the moment they're uploaded.
      </p>
    </div>
  );
}
