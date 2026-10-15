import { CalendarDays, CircleCheck, CirclePause, Images, type LucideIcon } from "lucide-react";
import type { ReactNode, Ref } from "react";

import { cn } from "../ui/cn";
import { pluralize, timeAgo } from "../../lib/format";
import type { EventPublic } from "../../lib/types";

type PillTone = "positive" | "warning" | "neutral";

const PILL_TONES: Record<PillTone, string> = {
  positive: "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-300 dark:ring-emerald-900",
  warning: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/50 dark:text-amber-300 dark:ring-amber-900",
  neutral: "bg-white text-stone-700 ring-stone-200 dark:bg-stone-900 dark:text-stone-300 dark:ring-stone-800",
};

function Pill({ tone = "neutral", icon: Icon, children }: { tone?: PillTone; icon: LucideIcon; children: ReactNode }) {
  return (
    <li className={cn("inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-semibold ring-1", PILL_TONES[tone])}>
      <Icon className="size-4" aria-hidden="true" />
      {children}
    </li>
  );
}

/** ``titleRef`` receives the page heading, which can take focus when something below it is dismissed. */
export function EventHeader({ event, titleRef }: { event: EventPublic; titleRef?: Ref<HTMLHeadingElement> }) {
  return (
    <div className="animate-fade-in">
      <p className="text-sm font-bold uppercase tracking-wider text-brand-700 dark:text-brand-400">Host dashboard</p>
      <h1
        ref={titleRef}
        tabIndex={-1}
        className="mt-1 rounded-md text-3xl font-extrabold tracking-tight text-balance wrap-break-word focus:outline-none sm:text-4xl"
      >
        {event.name}
      </h1>
      <ul className="mt-4 flex flex-wrap gap-2" aria-label="Event summary">
        {event.uploads_open ? (
          <Pill tone="positive" icon={CircleCheck}>
            Uploads open
          </Pill>
        ) : (
          <Pill tone="warning" icon={CirclePause}>
            Uploads closed
          </Pill>
        )}
        <Pill icon={Images}>{pluralize(event.photo_count, "photo")}</Pill>
        <Pill icon={CalendarDays}>
          <time dateTime={event.created_at}>Created {timeAgo(event.created_at)}</time>
        </Pill>
      </ul>
    </div>
  );
}
