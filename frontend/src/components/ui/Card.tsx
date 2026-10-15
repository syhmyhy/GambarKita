import type { HTMLAttributes } from "react";

import { cn } from "./cn";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-[var(--radius-card)] bg-white p-5 shadow-sm ring-1 ring-stone-200/80 sm:p-6",
        "dark:bg-stone-900 dark:ring-stone-800",
        className,
      )}
      {...rest}
    />
  );
}

export function CardTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("text-lg font-bold tracking-tight", className)} {...rest} />;
}

export function CardDescription({ className, ...rest }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("mt-1 text-sm text-stone-600 dark:text-stone-400", className)} {...rest} />;
}
