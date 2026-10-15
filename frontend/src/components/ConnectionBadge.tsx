import type { ConnectionStatus } from "../lib/realtime";
import { cn } from "./ui/cn";

const LABELS: Record<ConnectionStatus, string> = {
  open: "Live",
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
  offline: "Offline",
  closed: "Disconnected",
};

/** Small pill showing whether live updates are flowing. */
export function ConnectionBadge({ status, className, onDark }: { status: ConnectionStatus; className?: string; onDark?: boolean }) {
  const live = status === "open";
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn(
        "inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold",
        onDark
          ? "bg-white/10 text-white ring-1 ring-white/15 backdrop-blur"
          : "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
        className,
      )}
    >
      <span className="relative flex size-2">
        {live && <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-75" />}
        <span className={cn("relative inline-flex size-2 rounded-full", live ? "bg-emerald-500" : "bg-amber-500")} />
      </span>
      {LABELS[status]}
    </span>
  );
}
