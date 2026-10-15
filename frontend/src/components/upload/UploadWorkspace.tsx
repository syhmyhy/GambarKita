import { ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { usePublicConfig } from "../../hooks/usePublicConfig";
import { useWakeLock } from "../../hooks/useWakeLock";
import type { ApiError } from "../../lib/api";
import { pluralize } from "../../lib/format";
import type { PrepLimits } from "../../lib/image";
import { guestName } from "../../lib/storage";
import type { EventPublic, Photo } from "../../lib/types";
import { Card } from "../ui/Card";
import { useToast } from "../ui/Toast";
import { EventIntro } from "./EventIntro";
import { GuestDetailsFields } from "./GuestDetailsFields";
import { PhotoPickers } from "./PhotoPickers";
import { QueueAnnouncer, QueueProgress } from "./QueueProgress";
import { type PickedPhoto, type PickNote, ShareConfirmDialog } from "./ShareConfirmDialog";
import { UploadDoneCard } from "./UploadDoneCard";
import { UploadQueueGrid } from "./UploadQueueGrid";
import { MAX_FILES_PER_SELECTION, summarizeQueue } from "./uploadQueue";
import type { GuestAllowance } from "./useGuestQuota";
import { useLeaveWarning } from "./useLeaveWarning";
import { useUploadQueue } from "./useUploadQueue";

/** What the guest may still add; ``open`` already leaves out photos that are still sending. */
function allowanceHint(limit: number | null, remaining: number | null, open: number | null): string | null {
  if (limit === null || remaining === null || open === null) return null;
  if (open === 0) return remaining === 1 ? "Your last photo is on its way" : `Your last ${remaining} photos are on their way`;
  return open >= limit ? `You can share up to ${pluralize(limit, "photo")}` : `You can share ${pluralize(open, "more photo")}`;
}

let nextPickId = 0;

function releasePreviews(photos: readonly PickedPhoto[]): void {
  for (const photo of photos) URL.revokeObjectURL(photo.previewUrl);
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

interface UploadWorkspaceProps {
  event: EventPublic;
  /** Photos this guest has uploaded since opening the page. */
  sharedCount: number;
  /** How many more photos this guest may share, when the server limits it. */
  allowance: Pick<GuestAllowance, "limit" | "remaining">;
  onUploaded: (photo: Photo) => void;
  onStopped: (error: ApiError) => void;
}

/** The upload screen for an event that accepts photos. */
export function UploadWorkspace({ event, sharedCount, allowance, onUploaded, onStopped }: UploadWorkspaceProps) {
  const { config } = usePublicConfig();
  const toast = useToast();
  const [name, setName] = useState(guestName.get);
  const [caption, setCaption] = useState("");
  /** Counts shared selections, to bring the uploads into view after each one. */
  const [picks, setPicks] = useState(0);
  const uploadsRef = useRef<HTMLElement>(null);
  /** Photos picked but not yet confirmed: nothing uploads until the guest says so. */
  const [toShare, setToShare] = useState<readonly PickedPhoto[]>([]);
  /** Why fewer photos are offered than the guest picked (shown in the confirmation). */
  const [pickNote, setPickNote] = useState<PickNote | null>(null);
  // Kept in step with ``toShare`` (both set in handlers) so leaving the page can release the previews.
  const toShareRef = useRef<readonly PickedPhoto[]>([]);
  const updateToShare = (next: readonly PickedPhoto[]) => {
    toShareRef.current = next;
    setToShare(next);
  };
  useEffect(() => () => releasePreviews(toShareRef.current), []);

  const { max_upload_bytes: maxBytes, allowed_mime_types: allowedTypes } = config;
  const limits = useMemo<PrepLimits>(() => ({ maxBytes, allowedTypes }), [maxBytes, allowedTypes]);
  const queue = useUploadQueue(event.id, limits, { onUploaded, onStopped });
  const summary = summarizeQueue(queue.items);
  const busy = summary.active > 0;

  useLeaveWarning(busy);
  // A phone that falls asleep mid-upload drops the connection.
  useWakeLock(busy);

  // On a phone the uploads start below the fold: without this, nothing visibly happens after a pick.
  useEffect(() => {
    if (picks === 0) return;
    uploadsRef.current?.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  }, [picks]);

  const handleNameChange = (value: string) => {
    setName(value);
    guestName.set(value);
  };

  const { limit, remaining } = allowance;
  // Photos still sending will use up some of what's left.
  const open = remaining === null ? null : Math.max(0, remaining - summary.active);
  const hint = allowanceHint(limit, remaining, open);

  const handleFiles = (picked: File[]) => {
    const limitText = limit === null ? "" : `Each guest can share up to ${pluralize(limit, "photo")}.`;
    if (open === 0) {
      toast.info("You've picked all the photos you can share", limitText);
      return;
    }
    const files = picked.slice(0, Math.min(MAX_FILES_PER_SELECTION, open ?? MAX_FILES_PER_SELECTION));
    // Said in the confirmation rather than a toast, which the open dialog would cover.
    let note: PickNote | null = null;
    if (picked.length > files.length) {
      if (open !== null && open <= MAX_FILES_PER_SELECTION) {
        note = {
          title: `You can add ${pluralize(open, "more photo")}`,
          message: `Adding ${pluralize(open, "photo")} of the ${picked.length} you picked. ${limitText}`,
        };
      } else {
        const rest =
          open === null
            ? "Send the rest in another go."
            : `You can send ${pluralize(open - files.length, "more photo")} after these.`;
        note = { title: `Adding the first ${MAX_FILES_PER_SELECTION} photos`, message: `You picked ${picked.length}. ${rest}` };
      }
    }
    setPickNote(note);
    updateToShare(files.map((file) => ({ id: `pick-${nextPickId++}`, file, previewUrl: URL.createObjectURL(file) })));
  };

  const shareConfirmed = () => {
    guestName.set(name);
    // The queue takes over the previews (and releases them later).
    queue.add(
      toShare.map((photo) => photo.file),
      { uploaderName: name.trim(), caption: caption.trim() },
      toShare.map((photo) => photo.previewUrl),
    );
    updateToShare([]);
    setCaption("");
    setPicks((count) => count + 1);
  };

  const cancelShare = () => {
    releasePreviews(toShare);
    updateToShare([]);
  };

  const removeFromShare = (id: string) => {
    releasePreviews(toShare.filter((photo) => photo.id === id));
    updateToShare(toShare.filter((photo) => photo.id !== id));
  };

  return (
    <div className="space-y-8">
      <EventIntro event={event} sharedCount={sharedCount} />

      <Card className="animate-slide-up space-y-6">
        <GuestDetailsFields
          name={name}
          onNameChange={handleNameChange}
          caption={caption}
          onCaptionChange={setCaption}
          maxNameLength={config.max_uploader_name_length}
          maxCaptionLength={config.max_caption_length}
        />
        {/* No picking while the last allowed photos are sending: a camera shot taken now would be lost. */}
        <PhotoPickers onFiles={handleFiles} disabled={open === 0} />
        {hint && <p className="text-center text-sm font-medium text-stone-600 dark:text-stone-400">{hint}</p>}
      </Card>

      {toShare.length > 0 && (
        <ShareConfirmDialog
          photos={toShare}
          note={pickNote}
          onRemove={removeFromShare}
          onConfirm={shareConfirmed}
          onCancel={cancelShare}
        />
      )}

      <QueueAnnouncer summary={summary} />
      {queue.items.length > 0 && (
        <section ref={uploadsRef} aria-label="Your uploads" className="scroll-mt-20">
          {summary.allDone ? (
            <UploadDoneCard eventId={event.id} sharedCount={sharedCount} />
          ) : (
            <QueueProgress summary={summary} onRetryAll={queue.retryAll} />
          )}
          <UploadQueueGrid items={queue.items} onRetry={queue.retry} onRemove={queue.remove} />
        </section>
      )}

      <p className="flex items-center justify-center gap-2 text-center text-sm text-stone-500 dark:text-stone-400">
        <ShieldCheck className="size-4 shrink-0" aria-hidden="true" />
        Photos are processed to remove location data.
      </p>
    </div>
  );
}
