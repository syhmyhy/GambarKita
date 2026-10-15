import { ImagePlus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Spinner } from "../ui/Spinner";
import { useToast } from "../ui/Toast";
import { cn } from "../ui/cn";
import { api } from "../../lib/api";
import { pluralize } from "../../lib/format";
import type { Photo } from "../../lib/types";
import { PhotoTile, uploaderLabel } from "./PhotoTile";
import { hasErrorCode, type HostSession } from "./session";

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
  const [target, setTarget] = useState<Photo | null>(null);
  const [deleting, setDeleting] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const refocusHeading = useRef(false);

  // The deleted photo's button is gone: once the dialog has closed (child
  // effects run first), continue from the section heading instead of <body>.
  useEffect(() => {
    if (target === null && refocusHeading.current) {
      refocusHeading.current = false;
      headingRef.current?.focus();
    }
  });

  const confirmDelete = async () => {
    if (!target) return;
    setDeleting(true);
    try {
      await api.deletePhoto(session.eventId, target.id, session.token);
      removePhoto(target.id);
      refocusHeading.current = true;
      toast.success("Photo deleted");
    } catch (error) {
      if (hasErrorCode(error, "PHOTO_NOT_FOUND")) {
        removePhoto(target.id);
        refocusHeading.current = true;
        toast.info("Already deleted", "That photo had already been removed.");
      } else {
        session.reportError(error);
      }
    } finally {
      setDeleting(false);
      setTarget(null);
    }
  };

  const latest = latestLiveId ? photos.find((photo) => photo.id === latestLiveId) : undefined;

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
        <p className="text-sm font-medium text-stone-500 dark:text-stone-400">{pluralize(photoCount, "photo")}</p>
      </div>
      <p className="sr-only" aria-live="polite">
        {latest ? `New photo from ${uploaderLabel(latest)}` : ""}
      </p>

      {photos.length === 0 ? (
        initialLoading ? (
          <div className="mt-4 flex justify-center py-16 text-stone-500" role="status">
            <Spinner className="size-6" label="Loading photos" />
          </div>
        ) : (
          <EmptyPhotos />
        )
      ) : (
        <ul className="mt-4 grid grid-cols-2 gap-x-3 gap-y-5 sm:grid-cols-3 sm:gap-x-4 xl:grid-cols-4">
          {photos.map((photo) => (
            <PhotoTile key={photo.id} photo={photo} isNew={photo.id === latestLiveId} onDelete={setTarget} />
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

      <ConfirmDialog
        open={target !== null}
        title="Delete this photo?"
        description="It will be removed from the wall and the event for everyone. This can't be undone."
        confirmLabel="Delete photo"
        destructive
        busy={deleting}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setTarget(null)}
      />
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
