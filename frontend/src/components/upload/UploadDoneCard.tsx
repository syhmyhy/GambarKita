import { Images, PartyPopper } from "lucide-react";

import { routes } from "../../lib/env";
import { pluralize } from "../../lib/format";
import { ButtonLink } from "../ui/Button";
import { Card, CardDescription, CardTitle } from "../ui/Card";

export function UploadDoneCard({ eventId, sharedCount }: { eventId: string; sharedCount: number }) {
  return (
    <Card className="mb-4 animate-pop-in text-center">
      <span className="mx-auto grid size-14 place-items-center rounded-2xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/60 dark:text-emerald-400">
        <PartyPopper className="size-7" aria-hidden="true" />
      </span>
      <CardTitle className="mt-4">Your photos are on the wall!</CardTitle>
      <CardDescription>
        {sharedCount > 0 ? `You've shared ${pluralize(sharedCount, "photo")}. Thank you!` : "Thank you for sharing!"}
      </CardDescription>
      <ButtonLink
        to={routes.wall(eventId)}
        className="mt-5 w-full sm:w-auto"
        icon={<Images className="size-4" aria-hidden="true" />}
      >
        See the live wall
      </ButtonLink>
      <p className="mt-4 text-sm text-stone-500 dark:text-stone-400">
        Got more? Share more anytime with the buttons above.
      </p>
    </Card>
  );
}
