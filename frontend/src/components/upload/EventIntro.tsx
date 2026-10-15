import { Heart, Images } from "lucide-react";

import { pluralize } from "../../lib/format";
import type { EventPublic } from "../../lib/types";

export function EventIntro({ event, sharedCount }: { event: EventPublic; sharedCount: number }) {
  return (
    <section className="text-center">
      <h1 className="text-3xl font-extrabold tracking-tight text-balance break-words sm:text-4xl">{event.name}</h1>
      <p className="mx-auto mt-2 max-w-md text-stone-600 text-pretty dark:text-stone-400">
        Share your photos — they'll appear on the live wall.
      </p>
      <div className="mt-4 flex flex-wrap items-center justify-center gap-2 text-sm font-semibold">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-3 py-1 text-stone-700 ring-1 ring-stone-200/80 dark:bg-stone-900 dark:text-stone-300 dark:ring-stone-800">
          <Images className="size-4 text-brand-600 dark:text-brand-400" aria-hidden="true" />
          {event.photo_count > 0 ? `${pluralize(event.photo_count, "photo")} on the wall` : "Be the first to share a photo"}
        </span>
        {sharedCount > 0 && (
          <span className="inline-flex animate-pop-in items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-brand-700 ring-1 ring-brand-100 dark:bg-brand-950/50 dark:text-brand-300 dark:ring-brand-900">
            <Heart className="size-4" aria-hidden="true" />
            {sharedCount.toLocaleString()} from you
          </span>
        )}
      </div>
    </section>
  );
}
