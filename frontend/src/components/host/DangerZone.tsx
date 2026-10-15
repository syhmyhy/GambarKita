import { Trash2 } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { cn } from "../ui/cn";
import { api } from "../../lib/api";
import { pluralize } from "../../lib/format";
import { hasErrorCode, type HostSession } from "./session";

interface DangerZoneProps {
  session: HostSession;
  eventName: string;
  photoCount: number;
  /** Called with true when the delete request starts, and with false if it fails. */
  onDeletingChange: (deleting: boolean) => void;
  onDeleted: () => void;
  className?: string;
}

export function DangerZone({ session, eventName, photoCount, onDeletingChange, onDeleted, className }: DangerZoneProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const titleId = useId();

  const confirm = async () => {
    setBusy(true);
    onDeletingChange(true);
    try {
      await api.deleteEvent(session.eventId, session.token);
      onDeleted();
    } catch (error) {
      if (hasErrorCode(error, "EVENT_NOT_FOUND")) {
        onDeleted();
        return;
      }
      onDeletingChange(false);
      // Close first: an error toast would sit behind the modal's backdrop.
      setOpen(false);
      session.reportError(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-labelledby={titleId}
      className={cn(
        "rounded-[var(--radius-card)] bg-white p-5 shadow-sm ring-1 ring-red-200 sm:p-6 dark:bg-stone-900 dark:ring-red-900/70",
        className,
      )}
    >
      <h2 id={titleId} className="text-lg font-bold tracking-tight text-red-700 dark:text-red-400">
        Danger zone
      </h2>
      <p className="mt-1 text-sm text-stone-600 dark:text-stone-400">
        Deleting the event removes it and all of its photos for everyone, right away. This can't be undone.
      </p>
      <Button variant="danger" className="mt-4" icon={<Trash2 className="size-4" aria-hidden="true" />} onClick={() => setOpen(true)}>
        Delete event
      </Button>
      <ConfirmDialog
        open={open}
        title="Delete this event?"
        description={`“${eventName}” and its ${pluralize(photoCount, "photo")} will be permanently deleted. Guests will no longer be able to open it.`}
        confirmLabel="Delete forever"
        confirmText="delete"
        destructive
        busy={busy}
        onConfirm={() => void confirm()}
        onCancel={() => setOpen(false)}
      />
    </section>
  );
}
