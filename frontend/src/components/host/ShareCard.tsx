import { ExternalLink } from "lucide-react";
import { useId } from "react";

import { CopyButton } from "../CopyButton";
import { EventQrCode } from "../EventQrCode";
import { ButtonLink } from "../ui/Button";
import { Card, CardDescription, CardTitle } from "../ui/Card";
import { guestUploadUrl, routes } from "../../lib/env";
import type { EventPublic } from "../../lib/types";

export function ShareCard({ event }: { event: EventPublic }) {
  // Not simply this page's origin: a "localhost" link wouldn't open on guests' phones.
  const uploadUrl = guestUploadUrl(event);
  const inputId = useId();

  return (
    <Card>
      <CardTitle>Share with guests</CardTitle>
      <CardDescription>Guests scan the code with their phone camera to add photos. No app or sign-up needed.</CardDescription>

      <EventQrCode className="mt-5" url={uploadUrl} eventName={event.name} size={220} showActions />

      <div className="mt-6 border-t border-stone-200 pt-5 dark:border-stone-800">
        <label htmlFor={inputId} className="text-sm font-semibold text-stone-800 dark:text-stone-200">
          Upload link
        </label>
        <input
          id={inputId}
          type="text"
          readOnly
          value={uploadUrl}
          onFocus={(focusEvent) => focusEvent.currentTarget.select()}
          className="mt-1.5 h-11 w-full truncate rounded-xl bg-stone-50 px-3 font-mono text-sm text-stone-700 ring-1 ring-stone-200 focus:outline-none focus:ring-2 focus:ring-brand-500 dark:bg-stone-950 dark:text-stone-300 dark:ring-stone-700"
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <CopyButton text={uploadUrl} label="Copy link" size="md" />
          <ButtonLink
            to={routes.upload(event.id)}
            target="_blank"
            rel="noreferrer"
            variant="secondary"
            icon={<ExternalLink className="size-4" aria-hidden="true" />}
          >
            Open upload page
          </ButtonLink>
        </div>
      </div>
    </Card>
  );
}
