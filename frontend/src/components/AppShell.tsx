import type { ReactNode } from "react";
import { Link } from "react-router";

import { Logo } from "./Logo";
import { cn } from "./ui/cn";

const SAFE_X =
  "pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] " +
  "sm:pl-[max(1.5rem,env(safe-area-inset-left))] sm:pr-[max(1.5rem,env(safe-area-inset-right))]";

/**
 * Standard page chrome: slim header and a centered content column.
 * Guest-facing pages pass ``homeLink={false}`` so the logo doesn't lead guests to the home page.
 */
export function AppShell({
  children,
  headerRight,
  width = "md",
  homeLink = true,
}: {
  children: ReactNode;
  headerRight?: ReactNode;
  width?: "sm" | "md" | "lg" | "xl";
  homeLink?: boolean;
}) {
  const max = { sm: "max-w-lg", md: "max-w-2xl", lg: "max-w-4xl", xl: "max-w-6xl" }[width];
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 border-b border-stone-200/70 bg-stone-50/80 backdrop-blur-lg dark:border-stone-800/70 dark:bg-stone-950/80">
        <div className={cn("mx-auto flex h-16 items-center justify-between gap-4", SAFE_X, max)}>
          {homeLink ? (
            <Link to="/" className="rounded-lg" aria-label="GambarKita home">
              <Logo />
            </Link>
          ) : (
            <Logo />
          )}
          {headerRight}
        </div>
      </header>
      <main id="main" className={cn("mx-auto w-full flex-1 pb-16 pt-6 sm:pt-10", SAFE_X, max)}>
        {children}
      </main>
    </div>
  );
}
