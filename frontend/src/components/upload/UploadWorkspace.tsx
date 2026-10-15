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
  /** Why the guest can't share every photo they picked (shown in the confirmation). */
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
  // Most photos one confirmation may share: the per-pick cap, or the guest's allowance if lower.
  const maxShare = Math.min(MAX_FILES_PER_SELECTION, open ?? MAX_FILES_PER_SELECTION);

  /**
   * Adds ``files`` to the photos shown for confirmation (at most MAX_FILES_PER_SELECTION).
   * The first ones fill the free places; the rest are shown unchosen, for the guest to swap in.
   */
  const withPicked = (current: readonly PickedPhoto[], files: readonly File[]) => {
    const shown = files.slice(0, Math.max(0, MAX_FILES_PER_SELECTION - current.length));
    const free = maxShare - current.filter((photo) => photo.selected).length;
    const added = shown.map(
      (file, index): PickedPhoto => ({
        id: `pick-${nextPickId++}`,
        file,
        previewUrl: URL.createObjectURL(file),
        selected: index < free,
      }),
    );
    return { next: [...current, ...added], left: files.length - shown.length };
  };

  const handleFiles = (picked: File[]) => {
    if (open === 0) {
      const limitText = limit === null ? "" : `Each guest can share up to ${pluralize(limit, "photo")}.`;
      toast.info("You've picked all the photos you can share", limitText);
      return;
    }
    const { next, left } = withPicked([], picked);
    // Said in the confirmation rather than a toast, which the open dialog would cover.
    let note: PickNote | null = null;
    if (picked.length > maxShare) {
      if (open !== null && open <= MAX_FILES_PER_SELECTION) {
        const which = open === 1 ? "which one" : `which ${open}`;
        const hidden = left > 0 ? ` Only the first ${MAX_FILES_PER_SELECTION} are shown.` : "";
        note = {
          // Same words as the hint under the pickers ("up to 7" for a new guest, "2 more" later).
          title: hint ?? `You can share ${pluralize(open, "more photo")}`,
          message: `You picked ${picked.length}. Tap the photos to choose ${which} to share.${hidden}`,
        };
      } else {
        const rest =
          open === null
            ? "Send the rest in another go."
            : `You can send ${pluralize(open - maxShare, "more photo")} after these.`;
        note = { title: `Showing the first ${MAX_FILES_PER_SELECTION} photos`, message: `You picked ${picked.length}. ${rest}` };
      }
    }
    setPickNote(note);
    updateToShare(next);
  };

  const addToShare = (files: File[]) => {
    const { next, left } = withPicked(toShare, files);
    if (left > 0) {
      setPickNote({
        title: `You can pick up to ${MAX_FILES_PER_SELECTION} photos at a time`,
        message: `${pluralize(left, "photo")} didn't fit. Send ${left === 1 ? "it" : "them"} in another go.`,
      });
    }
    updateToShare(next);
  };

  const toggleShare = (id: string) => {
    updateToShare(toShare.map((photo) => (photo.id === id ? { ...photo, selected: !photo.selected } : photo)));
  };

  const shareConfirmed = () => {
    const chosen = toShare.filter((photo) => photo.selected);
    releasePreviews(toShare.filter((photo) => !photo.selected));
    guestName.set(name);
    // The queue takes over the chosen photos' previews (and releases them later).
    queue.add(
      chosen.map((photo) => photo.file),
      { uploaderName: name.trim(), caption: caption.trim() },
      chosen.map((photo) => photo.previewUrl),
    );
    updateToShare([]);
    setCaption("");
    setPicks((count) => count + 1);
  };

  const cancelShare = () => {
    releasePreviews(toShare);
    updateToShare([]);
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
        <PhotoPickers onFiles={handleFiles} disabled={open === 0} maxFiles={open === null ? null : maxShare} />
        {hint && <p className="text-center text-sm font-medium text-stone-600 dark:text-stone-400">{hint}</p>}
      </Card>

      {toShare.length > 0 && (
        <ShareConfirmDialog
          photos={toShare}
          maxSelected={maxShare}
          limited={open !== null}
          canAddMore={toShare.length < MAX_FILES_PER_SELECTION}
          note={pickNote}
          onToggle={toggleShare}
          onAddFiles={addToShare}
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
        Your photo's location is removed before it's saved or shown.
      </p>
    </div>
  );
}
