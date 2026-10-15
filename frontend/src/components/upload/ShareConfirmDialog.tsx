import { Check, Plus } from "lucide-react";
import { type ChangeEvent, useRef, useState } from "react";

import { pluralize } from "../../lib/format";
import { cn } from "../ui/cn";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Preview } from "./QueueTile";

/** A photo the guest picked but hasn't confirmed yet. Only ``selected`` ones are shared. */
export interface PickedPhoto {
  readonly id: string;
  readonly file: File;
  readonly previewUrl: string;
  readonly selected: boolean;
}

/** Why the guest can't share every photo they picked. */
export interface PickNote {
  readonly title: string;
  readonly message: string;
}

interface ShareConfirmDialogProps {
  photos: readonly PickedPhoto[];
  /** Most photos this confirmation may share. */
  maxSelected: number;
  /** Whether a per-guest limit applies (shows the "3 of 6 chosen" count). */
  limited: boolean;
  /** Whether more photos may be shown (the per-pick cap isn't reached). */
  canAddMore: boolean;
  note?: PickNote | null;
  onToggle: (id: string) => void;
  onAddFiles: (files: File[]) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Shows the picked photos and asks the guest to confirm before they go up on the live wall.
 * Guests tap photos to choose which ones to share, and can add more while there's room.
 */
export function ShareConfirmDialog({
  photos,
  maxSelected,
  limited,
  canAddMore,
  note,
  onToggle,
  onAddFiles,
  onConfirm,
  onCancel,
}: ShareConfirmDialogProps) {
  const addInput = useRef<HTMLInputElement>(null);
  /** Set when the guest taps an unchosen photo while every place is taken. */
  const [full, setFull] = useState(false);
  const selected = photos.filter((photo) => photo.selected).length;
  const free = maxSelected - selected;

  const title =
    selected === 0
      ? "Choose photos to share"
      : selected === 1
        ? "Share this photo on the live wall?"
        : `Share these ${selected} photos on the live wall?`;

  const toggle = (photo: PickedPhoto) => {
    if (!photo.selected && free <= 0) {
      setFull(true);
      return;
    }
    setFull(false);
    onToggle(photo.id);
  };

  const handleAdd = (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    // Reset so picking the same photo again still fires "change".
    input.value = "";
    setFull(false);
    if (files.length > 0) onAddFiles(files);
  };

  return (
    <ConfirmDialog
      open
      title={title}
      description={`Everyone at the event will see ${selected === 1 ? "it" : "them"}. Only the host can remove a photo once it's shared.`}
      confirmLabel={`Share ${pluralize(selected, "photo")}`}
      confirmDisabled={selected === 0}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      {note && (
        <div
          role="note"
          className="mt-4 rounded-xl bg-brand-50 px-4 py-3 text-sm text-brand-900 ring-1 ring-brand-100 dark:bg-brand-950/50 dark:text-brand-200 dark:ring-brand-900"
        >
          <p className="font-semibold">{note.title}</p>
          <p className="mt-0.5">{note.message}</p>
        </div>
      )}
      {limited && (
        <p aria-live="polite" className={cn("mt-4 text-sm font-semibold", full ? "text-red-600 dark:text-red-400" : "text-stone-600 dark:text-stone-300")}>
          {full
            ? `You can share up to ${pluralize(maxSelected, "photo")}. Tap a chosen photo to take it out first.`
            : `${selected} of ${maxSelected} chosen`}
        </p>
      )}
      <ul aria-label="Photos to share" className="mt-3 grid max-h-[50dvh] grid-cols-3 gap-2 overflow-y-auto p-0.5">
        {photos.map((photo, index) => (
          <li key={photo.id}>
            <button
              type="button"
              aria-pressed={photo.selected}
              aria-label={`Share photo ${index + 1}`}
              onClick={() => toggle(photo)}
              className={cn(
                "relative block aspect-square w-full overflow-hidden rounded-xl bg-stone-200 ring-1 ring-stone-200/80 transition dark:bg-stone-800 dark:ring-stone-800",
                photo.selected && "ring-2 ring-brand-500 dark:ring-brand-400",
              )}
            >
              <Preview src={photo.previewUrl} alt="" className={cn("transition", !photo.selected && "opacity-35")} />
              <span
                aria-hidden="true"
                className={cn(
                  "absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-full ring-2 ring-white",
                  photo.selected ? "bg-brand-600 text-white" : "bg-stone-950/40",
                )}
              >
                {photo.selected && <Check className="size-4" />}
              </span>
            </button>
          </li>
        ))}
        {canAddMore && free > 0 && (
          <li>
            <button
              type="button"
              onClick={() => addInput.current?.click()}
              className="grid aspect-square w-full place-items-center rounded-xl border-2 border-dashed border-stone-300 text-stone-500 transition hover:border-brand-400 hover:text-brand-600 dark:border-stone-700 dark:text-stone-400"
            >
              <span className="flex flex-col items-center gap-1 text-xs font-semibold">
                <Plus className="size-6" aria-hidden="true" />
                {free === 1 ? "Add a photo" : "Add photos"}
              </span>
            </button>
            <input
              ref={addInput}
              type="file"
              accept="image/*"
              multiple={free > 1}
              hidden
              aria-label="Add photos from your gallery"
              onChange={handleAdd}
            />
          </li>
        )}
      </ul>
    </ConfirmDialog>
  );
}
