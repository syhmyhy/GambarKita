import { type ReactNode, useEffect, useId, useRef, useState } from "react";

import { Button } from "./Button";
import { TextField } from "./TextField";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  /** Require typing this exact text before confirming (for destructive actions). */
  confirmText?: string;
  destructive?: boolean;
  busy?: boolean;
  /** Extra content between the description and the buttons (e.g. previews). */
  children?: ReactNode;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Accessible confirmation built on the native <dialog> element: focus trapping,
 * Escape to cancel and background inertness come from the browser.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  confirmText,
  destructive,
  busy,
  children,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setTyped("");
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  const canConfirm = !confirmText || typed.trim().toLowerCase() === confirmText.toLowerCase();

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-[var(--radius-card)] bg-white p-0 text-stone-900 shadow-2xl ring-1 ring-stone-200 backdrop:bg-stone-950/60 backdrop:backdrop-blur-sm dark:bg-stone-900 dark:text-stone-100 dark:ring-stone-700"
    >
      <form
        method="dialog"
        className="p-6"
        onSubmit={(event) => {
          event.preventDefault();
          if (canConfirm && !busy) onConfirm();
        }}
      >
        <h2 id={titleId} className="text-lg font-bold">
          {title}
        </h2>
        <p id={descriptionId} className="mt-2 text-sm text-stone-600 dark:text-stone-400">
          {description}
        </p>
        {children}
        {confirmText && (
          <TextField
            className="mt-4"
            label={`Type “${confirmText}” to confirm`}
            value={typed}
            onChange={setTyped}
            autoComplete="off"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
          />
        )}
        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button type="submit" variant={destructive ? "danger" : "primary"} disabled={!canConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </dialog>
  );
}
