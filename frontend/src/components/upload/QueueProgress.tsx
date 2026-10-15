import { RotateCcw } from "lucide-react";

import { pluralize } from "../../lib/format";
import { Button } from "../ui/Button";
import type { QueueSummary } from "./uploadQueue";

/** Screen-reader announcement of the queue, e.g. "2 of 5 photos uploaded". Always mounted so updates are announced. */
export function QueueAnnouncer({ summary }: { summary: QueueSummary }) {
  let text = "";
  if (summary.total > 0) {
    text = `${summary.done} of ${pluralize(summary.total, "photo")} uploaded`;
    if (summary.failed > 0) text += `, ${summary.failed} not sent`;
    if (summary.allDone) text += ". Your photos are on the wall!";
  }
  return (
    <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
      {text}
    </p>
  );
}

/** Visible heading and overall progress while photos are being sent (or some failed). */
export function QueueProgress({ summary, onRetryAll }: { summary: QueueSummary; onRetryAll: () => void }) {
  const sending = summary.active > 0;
  const percent = Math.round(summary.progress * 100);
  return (
    <div className="mb-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-bold tracking-tight">{sending ? "Sending your photos…" : "Some photos weren't sent"}</h2>
          <p className="text-sm text-stone-600 dark:text-stone-400">
            {summary.done} of {summary.total} uploaded
            {summary.failed > 0 && <span className="text-red-600 dark:text-red-400"> · {summary.failed} not sent</span>}
            {sending && summary.total > 1 && " · keep this page open"}
          </p>
        </div>
        {summary.retryable > 1 && (
          <Button variant="secondary" onClick={onRetryAll} icon={<RotateCcw className="size-4" aria-hidden="true" />}>
            Retry all
          </Button>
        )}
      </div>
      {sending && (
        <div className="mt-3 h-2 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-800" aria-hidden="true">
          <div
            className="h-full rounded-full bg-brand-500 transition-[width] duration-500"
            style={{ width: `${Math.max(percent, 3)}%` }}
          />
        </div>
      )}
    </div>
  );
}
