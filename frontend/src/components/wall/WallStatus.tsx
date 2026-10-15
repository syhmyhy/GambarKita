import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { LoadingScreen, StatusScreen } from "../ui/StatusScreen";

interface WallStatusProps {
  icon: LucideIcon;
  title: string;
  message: ReactNode;
  actions?: ReactNode;
  tone?: "neutral" | "danger";
}

/** A full-page message on the wall, which is always dark whatever the system theme. */
export function WallStatus(props: WallStatusProps) {
  return (
    <main id="main" className="grid min-h-dvh place-items-center bg-stone-950 text-white">
      <StatusScreen onDark className="animate-fade-in" {...props} />
    </main>
  );
}

export function WallLoading() {
  return (
    <main id="main" className="min-h-dvh bg-stone-950">
      <LoadingScreen label="Loading the live wall…" className="min-h-dvh" />
    </main>
  );
}
