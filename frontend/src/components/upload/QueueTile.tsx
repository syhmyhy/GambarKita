import { Check, Clock, ImageOff, RotateCcw, TriangleAlert, X } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Button } from "../ui/Button";
import { cn } from "../ui/cn";
import { Spinner } from "../ui/Spinner";
import { isActive, type UploadItem } from "./uploadQueue";

interface QueueTileProps {
  item: UploadItem;
  /** 1-based position, used in labels ("Photo 3"). */
  number: number;
  onRetry: (id: string) => void;
  onRemove: (id: string) => void;
}

export function QueueTile({ item, number, onRetry, onRemove }: QueueTileProps) {
  if (item.status === "error") return <ErrorTile item={item} number={number} onRetry={onRetry} onRemove={onRemove} />;

  return (
    <div className="relative aspect-square animate-fade-in overflow-hidden rounded-2xl bg-stone-200 ring-1 ring-stone-200/80 dark:bg-stone-800 dark:ring-stone-800">
      <Preview src={item.previewUrl} alt={`Photo ${number}`} />
      <StatusOverlay item={item} number={number} />
      {isActive(item) && (
        <button
          type="button"
          onClick={() => onRemove(item.id)}
          aria-label={`Cancel photo ${number}`}
          title="Cancel"
          className="group absolute right-0 top-0 grid size-11 place-items-center rounded-full focus-visible:outline-offset-[-4px] focus-visible:outline-white"
        >
          <span className="grid size-7 place-items-center rounded-full bg-stone-950/60 text-white backdrop-blur-sm transition group-hover:bg-stone-950/80">
            <X className="size-4" aria-hidden="true" />
          </span>
        </button>
      )}
    </div>
  );
}

/** The local preview. Some formats (e.g. HEIC outside Safari) can't be shown, so fall back to an icon. */
export function Preview({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) {
    return (
      <div role="img" aria-label={alt} className={cn("grid size-full place-items-center text-stone-400 dark:text-stone-500", className)}>
        <ImageOff className="size-8" aria-hidden="true" />
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      decoding="async"
      draggable={false}
      onError={() => setBroken(true)}
      className={cn("size-full object-cover", className)}
    />
  );
}

function OverlayPill({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-stone-950/65 px-2.5 py-1 text-xs font-semibold text-white backdrop-blur-sm">
      {children}
    </span>
  );
}

function StatusOverlay({ item, number }: { item: UploadItem; number: number }) {
  switch (item.status) {
    case "waiting":
      return (
        <div className="absolute inset-0 grid place-items-center bg-stone-950/35">
          <OverlayPill>
            {item.autoRetrying ? (
              <RotateCcw className="size-3.5" aria-hidden="true" />
            ) : (
              <Clock className="size-3.5" aria-hidden="true" />
            )}
            {item.autoRetrying ? "Retrying soon" : "Waiting"}
          </OverlayPill>
        </div>
      );
    case "preparing":
      return (
        <div className="absolute inset-0 grid place-items-center bg-stone-950/35">
          <OverlayPill>
            <Spinner className="size-3.5" />
            Preparing
          </OverlayPill>
        </div>
      );
    case "uploading": {
      const percent = Math.round(item.progress * 100);
      // The body is sent; the server is still saving and resizing the photo.
      const finishing = percent >= 100;
      return (
        <div className="absolute inset-0 bg-linear-to-t from-stone-950/60 via-stone-950/15 to-stone-950/10">
          <div className="absolute inset-0 grid place-items-center">
            <OverlayPill>
              {finishing ? (
                <>
                  <Spinner className="size-3.5" />
                  Finishing
                </>
              ) : (
                <span className="tabular-nums">{percent}%</span>
              )}
            </OverlayPill>
          </div>
          <div
            role="progressbar"
            aria-label={`Uploading photo ${number}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            className="absolute inset-x-2.5 bottom-2.5 h-1.5 overflow-hidden rounded-full bg-white/30"
          >
            <div className="h-full rounded-full bg-white transition-[width] duration-300" style={{ width: `${percent}%` }} />
          </div>
        </div>
      );
    }
    case "done":
      return (
        <span className="absolute bottom-2 right-2 grid size-8 animate-pop-in place-items-center rounded-full bg-emerald-500 text-white shadow-md ring-2 ring-white dark:ring-stone-900">
          <Check className="size-4.5" strokeWidth={3} aria-hidden="true" />
          <span className="sr-only">Uploaded</span>
        </span>
      );
    default:
      return null;
  }
}

function ErrorTile({ item, number, onRetry, onRemove }: QueueTileProps) {
  return (
    <div className="flex h-full animate-fade-in gap-3 rounded-2xl bg-red-50 p-2.5 ring-1 ring-red-200 dark:bg-red-950/40 dark:ring-red-900/60">
      <div className="relative size-24 shrink-0 overflow-hidden rounded-xl bg-stone-200 dark:bg-stone-800">
        <Preview src={item.previewUrl} alt={`Photo ${number}`} />
        <span className="absolute left-1.5 top-1.5 grid size-7 place-items-center rounded-full bg-red-600 text-white shadow ring-2 ring-white dark:ring-stone-900">
          <TriangleAlert className="size-4" aria-hidden="true" />
        </span>
      </div>
      <div className="flex min-w-0 flex-1 flex-col py-0.5">
        <p className="text-sm font-bold text-red-700 dark:text-red-300">{item.error?.title ?? "Not sent"}</p>
        {item.error?.message && (
          <p className="mt-0.5 text-xs/snug break-words text-stone-700 dark:text-stone-300">{item.error.message}</p>
        )}
        <div className="mt-auto flex flex-wrap gap-2 pt-2">
          {item.retryable && (
            <Button
              variant="secondary"
              onClick={() => onRetry(item.id)}
              aria-label={`Retry photo ${number}`}
              icon={<RotateCcw className="size-4" aria-hidden="true" />}
            >
              Retry
            </Button>
          )}
          <Button
            variant="ghost"
            onClick={() => onRemove(item.id)}
            aria-label={`Remove photo ${number}`}
            icon={<X className="size-4" aria-hidden="true" />}
          >
            Remove
          </Button>
        </div>
      </div>
    </div>
  );
}
