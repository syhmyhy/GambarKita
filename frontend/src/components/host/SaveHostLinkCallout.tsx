import { Eye, EyeOff, KeyRound, ShieldAlert } from "lucide-react";
import { useId, useState } from "react";

import { CopyButton } from "../CopyButton";
import { Button } from "../ui/Button";
import { cn } from "../ui/cn";
import type { EventPublic } from "../../lib/types";
import { hostDashboardUrl, hostLinkReminder, maskedHostDashboardUrl } from "./session";

const LINK_BOX =
  "min-w-0 flex-1 rounded-xl bg-white px-4 font-mono text-sm text-stone-800 ring-1 ring-brand-200 dark:bg-stone-950 dark:text-stone-200 dark:ring-brand-900";

interface SaveHostLinkCalloutProps {
  eventId: string;
  token: string;
  event: EventPublic | null;
  /** After "I've saved it" removes the callout, so focus can move somewhere sensible. */
  onDismiss?: () => void;
}

/**
 * Shown after creating an event (or on a new device): the host link is the
 * only way back in. The token stays hidden until asked for, because this
 * screen (right above the QR code) is often shown to guests or projected.
 */
export function SaveHostLinkCallout({ eventId, token, event, onDismiss }: SaveHostLinkCalloutProps) {
  const [dismissed, setDismissed] = useState(() => hostLinkReminder.isDismissed(eventId));
  const [revealed, setRevealed] = useState(false);
  const titleId = useId();
  if (dismissed) return null;

  const url = hostDashboardUrl(eventId, token, event);
  return (
    <section
      aria-labelledby={titleId}
      className="mt-6 animate-slide-up rounded-[var(--radius-card)] bg-brand-50 p-5 ring-1 ring-brand-200 sm:p-6 dark:bg-brand-950/40 dark:ring-brand-900"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand-600 text-white dark:bg-brand-500 dark:text-stone-950">
          <KeyRound className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id={titleId} className="text-lg font-bold tracking-tight">
            Save your private host link
          </h2>
          <p className="mt-1 text-sm text-stone-700 dark:text-stone-300">
            It's the only way back to this dashboard, from this or any other device. Keep it somewhere safe, like your notes
            or a message to yourself.
          </p>
          <p className="mt-3 flex items-start gap-2 text-sm font-medium text-red-700 dark:text-red-400">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            Anyone with this link can manage and delete the event. It can't be recovered if you lose it.
          </p>
          <div className="mt-4 flex items-center gap-2">
            {revealed ? (
              <input
                type="text"
                readOnly
                value={url}
                aria-label="Private host link"
                spellCheck={false}
                onFocus={(focusEvent) => focusEvent.currentTarget.select()}
                className={cn(LINK_BOX, "h-11 focus:outline-none focus:ring-2 focus:ring-brand-500")}
              />
            ) : (
              <p className={cn(LINK_BOX, "truncate py-3")}>{maskedHostDashboardUrl(eventId, token, event)}</p>
            )}
            <Button
              variant="ghost"
              aria-pressed={revealed}
              onClick={() => setRevealed((value) => !value)}
              icon={
                revealed ? <EyeOff className="size-4" aria-hidden="true" /> : <Eye className="size-4" aria-hidden="true" />
              }
            >
              {revealed ? "Hide" : "Show"}
            </Button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <CopyButton text={url} label="Copy host link" variant="primary" size="md" />
            <Button
              variant="ghost"
              onClick={() => {
                hostLinkReminder.dismiss(eventId);
                setDismissed(true);
                onDismiss?.();
              }}
            >
              I've saved it
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}
