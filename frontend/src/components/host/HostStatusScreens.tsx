import { CalendarX2, House, KeyRound, Link2Off, RefreshCw, Trash2, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import { AppShell } from "../AppShell";
import { Button, ButtonLink } from "../ui/Button";
import { LoadingScreen, StatusScreen } from "../ui/StatusScreen";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { routes } from "../../lib/env";
import { describeError } from "../../lib/errors";

function GuestLinks({ eventId }: { eventId: string }) {
  return (
    <>
      <ButtonLink to={routes.wall(eventId)} variant="secondary">
        View the wall
      </ButtonLink>
      <ButtonLink to={routes.upload(eventId)} variant="secondary">
        Upload photos
      </ButtonLink>
    </>
  );
}

const homeIcon = <House className="size-4" aria-hidden="true" />;

function Screen({ title, children }: { title: string; children: ReactNode }) {
  useDocumentTitle(title);
  return <AppShell>{children}</AppShell>;
}

export function HostLoading({ label = "Opening your dashboard…" }: { label?: string }) {
  useDocumentTitle("Host dashboard");
  return (
    <AppShell width="xl">
      <LoadingScreen label={label} />
    </AppShell>
  );
}

export function HostAccessNeeded({ eventId }: { eventId: string }) {
  return (
    <Screen title="Host access needed">
      <StatusScreen
        icon={KeyRound}
        title="Host access needed"
        message={
          <>
            <p>To manage this event, open the private host link you saved when you created it.</p>
            <p className="mt-2 text-sm">Just visiting? You can still see the photos or add your own.</p>
          </>
        }
        actions={<GuestLinks eventId={eventId} />}
      />
    </Screen>
  );
}

export function InvalidHostLink({ eventId }: { eventId: string }) {
  return (
    <Screen title="Host link not valid">
      <StatusScreen
        icon={Link2Off}
        tone="danger"
        title="This host link is no longer valid"
        message="It may be incomplete or belong to another event. Open the full private host link you saved when you created the event."
        actions={<GuestLinks eventId={eventId} />}
      />
    </Screen>
  );
}

export function HostEventNotFound() {
  return (
    <Screen title="Event not found">
      <StatusScreen
        icon={CalendarX2}
        title="Event not found"
        message="This event doesn't exist. It may have been deleted, or the link is wrong."
        actions={
          <ButtonLink to={routes.home()} icon={homeIcon}>
            Go to home
          </ButtonLink>
        }
      />
    </Screen>
  );
}

export function HostEventDeleted() {
  return (
    <Screen title="Event deleted">
      <StatusScreen
        icon={Trash2}
        title="This event was deleted"
        message="The event and all of its photos have been permanently removed."
        actions={
          <ButtonLink to={routes.home()} icon={homeIcon}>
            Go to home
          </ButtonLink>
        }
      />
    </Screen>
  );
}

export function HostLoadError({ title, error, onRetry }: { title: string; error: unknown; onRetry: () => void }) {
  const friendly = describeError(error);
  return (
    <Screen title={title}>
      <StatusScreen
        icon={WifiOff}
        tone="danger"
        title={title}
        message={friendly.message}
        actions={
          <Button onClick={onRetry} icon={<RefreshCw className="size-4" aria-hidden="true" />}>
            Try again
          </Button>
        }
      />
    </Screen>
  );
}
