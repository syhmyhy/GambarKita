import { CalendarX2, Heart, ImageOff, Images, Lock, RefreshCw, TriangleAlert, WifiOff } from "lucide-react";

import { ApiError } from "../../lib/api";
import { routes } from "../../lib/env";
import { describeError } from "../../lib/errors";
import { pluralize } from "../../lib/format";
import { Button, ButtonLink } from "../ui/Button";
import { StatusScreen } from "../ui/StatusScreen";

export function EventNotFoundScreen() {
  return (
    <StatusScreen
      icon={CalendarX2}
      title="Event not found"
      message="This event doesn't exist or has been removed. Check the link, or scan the QR code again."
    />
  );
}

export function EventLoadErrorScreen({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const offline = error instanceof ApiError && error.isNetworkError;
  return (
    <StatusScreen
      icon={offline ? WifiOff : TriangleAlert}
      tone="danger"
      title="Couldn't load this event"
      message={describeError(error).message}
      actions={
        <Button onClick={onRetry} icon={<RefreshCw className="size-4" aria-hidden="true" />}>
          Try again
        </Button>
      }
    />
  );
}

const ICONS = { closed: Lock, full: ImageOff, guest_limit: Heart };
const TITLES = {
  closed: "The host has closed uploads",
  full: "This event is full",
  guest_limit: "You've shared all your photos",
};

/**
 * This guest can't add photos: the host closed uploads, the event is full (its
 * photo limit is reached), or the guest has shared as many as each guest may.
 */
export function UploadsClosedScreen({
  variant = "closed",
  eventId,
  eventName,
  sharedCount,
  guestLimit = null,
  checking,
  onCheckAgain,
}: {
  variant?: "closed" | "full" | "guest_limit";
  eventId: string;
  eventName: string;
  sharedCount: number;
  /** Photos each guest may share (for the "guest_limit" variant), if known. */
  guestLimit?: number | null;
  checking: boolean;
  onCheckAgain: () => void;
}) {
  const name = <span className="font-semibold text-stone-800 dark:text-stone-200">{eventName}</span>;
  return (
    <StatusScreen
      icon={ICONS[variant]}
      title={TITLES[variant]}
      message={
        <>
          {variant === "full" ? (
            <p>The host's photo limit has been reached. You can still enjoy everyone's photos from {name} on the live wall.</p>
          ) : variant === "guest_limit" ? (
            <p>
              {guestLimit ? `Each guest can share up to ${pluralize(guestLimit, "photo")} at ` : "You've reached the photo limit for each guest at "}
              {name}. You can still enjoy everyone's photos on the live wall.
            </p>
          ) : (
            <p>New photos can't be added to {name} right now, but you can still enjoy everyone's photos on the live wall.</p>
          )}
          {sharedCount > 0 && (
            <p className="mt-3 font-semibold text-emerald-700 dark:text-emerald-400">
              Thank you! Your {pluralize(sharedCount, "photo")} made it to the wall.
            </p>
          )}
        </>
      }
      actions={
        <>
          <ButtonLink to={routes.wall(eventId)} icon={<Images className="size-4" aria-hidden="true" />}>
            View the photo wall
          </ButtonLink>
          {/* The host may reopen uploads, make room, or delete one of this guest's photos. */}
          <Button
            variant="secondary"
            loading={checking}
            onClick={onCheckAgain}
            icon={<RefreshCw className="size-4" aria-hidden="true" />}
          >
            Check again
          </Button>
        </>
      }
    />
  );
}
