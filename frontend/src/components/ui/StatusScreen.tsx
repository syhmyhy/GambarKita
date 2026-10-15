import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

interface StatusScreenProps {
  icon?: LucideIcon;
  title: string;
  message?: ReactNode;
  actions?: ReactNode;
  tone?: "neutral" | "danger";
  /** Force the dark look regardless of the system theme (the live wall is always dark). */
  onDark?: boolean;
  className?: string;
}

/** Centered full-area message: empty states, "event not found", errors. */
export function StatusScreen({ icon: Icon, title, message, actions, tone = "neutral", onDark, className }: StatusScreenProps) {
  return (
    <div className={cn("mx-auto flex max-w-md flex-col items-center px-6 py-16 text-center", className)}>
      {Icon && (
        <div
          className={cn(
            "mb-5 grid size-16 place-items-center rounded-2xl",
            onDark
              ? tone === "danger"
                ? "bg-white/10 text-red-300"
                : "bg-white/10 text-brand-300"
              : tone === "danger"
                ? "bg-red-50 text-red-600 dark:bg-red-950/60 dark:text-red-400"
                : "bg-brand-50 text-brand-600 dark:bg-brand-950/60 dark:text-brand-400",
          )}
        >
          <Icon className="size-8" aria-hidden="true" />
        </div>
      )}
      <h1 className="text-2xl font-bold tracking-tight text-balance">{title}</h1>
      {message && (
        <div className={cn("mt-2 text-pretty", onDark ? "text-stone-300" : "text-stone-600 dark:text-stone-400")}>{message}</div>
      )}
      {actions && <div className="mt-6 flex flex-wrap justify-center gap-3">{actions}</div>}
    </div>
  );
}

export function LoadingScreen({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div
      className={cn("flex flex-col items-center justify-center gap-3 py-24 text-stone-500 dark:text-stone-400", className)}
      role="status"
    >
      <Spinner className="size-7" />
      <span className="text-sm">{label}</span>
    </div>
  );
}
