import { Check } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { Button } from "../ui/Button";
import { Card, CardTitle } from "../ui/Card";
import { cn } from "../ui/cn";
import { TextField } from "../ui/TextField";
import { usePublicConfig } from "../../hooks/usePublicConfig";
import { describeError } from "../../lib/errors";
import type { EventPublic } from "../../lib/types";
import { hasErrorCode } from "./session";

interface SettingsCardProps {
  event: EventPublic;
  togglingUploads: boolean;
  onUploadsChange: (open: boolean) => void;
  onRename: (name: string) => Promise<string>;
  onError: (error: unknown) => void;
}

export function SettingsCard({ event, togglingUploads, onUploadsChange, onRename, onError }: SettingsCardProps) {
  return (
    <Card>
      <CardTitle>Settings</CardTitle>
      <UploadsSwitch open={event.uploads_open} busy={togglingUploads} onChange={onUploadsChange} />
      <RenameEventForm name={event.name} onRename={onRename} onError={onError} />
    </Card>
  );
}

function UploadsSwitch({ open, busy, onChange }: { open: boolean; busy: boolean; onChange: (open: boolean) => void }) {
  const labelId = useId();
  const descriptionId = useId();
  return (
    <div className="mt-5 flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p id={labelId} className="font-semibold">
          Accept uploads
        </p>
        <p id={descriptionId} className="mt-0.5 text-sm text-stone-600 dark:text-stone-400">
          {open ? "Guests can add new photos." : "Paused. Guests can still watch the wall."}
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={open}
        aria-labelledby={labelId}
        aria-describedby={descriptionId}
        aria-busy={busy || undefined}
        onClick={() => {
          if (!busy) onChange(!open);
        }}
        className="inline-flex h-11 shrink-0 items-center rounded-full px-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
      >
        <span
          className={cn(
            "relative h-7 w-12 rounded-full transition-colors",
            open ? "bg-emerald-500 dark:bg-emerald-600" : "bg-stone-300 dark:bg-stone-700",
            busy && "opacity-70",
          )}
        >
          <span
            className={cn(
              "absolute left-0.5 top-0.5 size-6 rounded-full bg-white shadow-sm transition-transform",
              open && "translate-x-5",
            )}
          />
        </span>
      </button>
    </div>
  );
}

function RenameEventForm({
  name,
  onRename,
  onError,
}: {
  name: string;
  onRename: (name: string) => Promise<string>;
  onError: (error: unknown) => void;
}) {
  const { config } = usePublicConfig();
  // null = not edited, so the field follows renames made elsewhere.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const value = draft ?? name;
  const trimmed = value.trim();
  const changed = trimmed !== name;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!trimmed) {
      setError("Give your event a name.");
      return;
    }
    if (!changed || saving) return;
    setSaving(true);
    try {
      await onRename(trimmed);
      setDraft(null);
    } catch (caught) {
      if (hasErrorCode(caught, "VALIDATION_ERROR")) setError(describeError(caught).message);
      else onError(caught);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="mt-6 border-t border-stone-200 pt-5 dark:border-stone-800" onSubmit={submit} noValidate>
      <TextField
        label="Event name"
        value={value}
        onChange={(next) => {
          setDraft(next);
          setError(null);
        }}
        maxLength={config.max_event_name_length}
        showCount
        error={error}
        autoComplete="off"
        enterKeyHint="done"
      />
      <Button
        type="submit"
        variant="secondary"
        className="mt-3"
        loading={saving}
        disabled={!changed}
        icon={<Check className="size-4" aria-hidden="true" />}
      >
        Save name
      </Button>
    </form>
  );
}
