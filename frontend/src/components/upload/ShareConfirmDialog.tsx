import { X } from "lucide-react";

import { pluralize } from "../../lib/format";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Preview } from "./QueueTile";

/** A photo the guest picked but hasn't confirmed yet. */
export interface PickedPhoto {
  readonly id: string;
  readonly file: File;
  readonly previewUrl: string;
}

/** Why fewer photos are offered than the guest picked. */
export interface PickNote {
  readonly title: string;
  readonly message: string;
}

interface ShareConfirmDialogProps {
  photos: readonly PickedPhoto[];
  note?: PickNote | null;
  onRemove: (id: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Shows the picked photos and asks the guest to confirm before they go up on the live wall. */
export function ShareConfirmDialog({ photos, note, onRemove, onConfirm, onCancel }: ShareConfirmDialogProps) {
  const count = photos.length;
  return (
    <ConfirmDialog
      open
      title={count === 1 ? "Share this photo on the live wall?" : `Share these ${count} photos on the live wall?`}
      description={`Everyone at the event will see ${count === 1 ? "it" : "them"}. Only the host can remove a photo once it's shared.`}
      confirmLabel={`Share ${pluralize(count, "photo")}`}
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
      <ul aria-label="Photos to share" className="mt-4 grid max-h-[50dvh] grid-cols-3 gap-2 overflow-y-auto">
        {photos.map((photo, index) => (
          <li
            key={photo.id}
            className="relative aspect-square overflow-hidden rounded-xl bg-stone-200 ring-1 ring-stone-200/80 dark:bg-stone-800 dark:ring-stone-800"
          >
            <Preview src={photo.previewUrl} alt={`Photo ${index + 1}`} />
            {/* With one photo, Cancel does the same. */}
            {count > 1 && (
              <button
                type="button"
                onClick={() => onRemove(photo.id)}
                aria-label={`Don't share photo ${index + 1}`}
                title="Don't share"
                className="group absolute right-0 top-0 grid size-10 place-items-center rounded-full focus-visible:outline-offset-[-4px] focus-visible:outline-white"
              >
                <span className="grid size-7 place-items-center rounded-full bg-stone-950/60 text-white backdrop-blur-sm transition group-hover:bg-stone-950/80">
                  <X className="size-4" aria-hidden="true" />
                </span>
              </button>
            )}
          </li>
        ))}
      </ul>
    </ConfirmDialog>
  );
}
