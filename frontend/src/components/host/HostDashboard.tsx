import { MonitorPlay } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";

import { AppShell } from "../AppShell";
import { ConnectionBadge } from "../ConnectionBadge";
import { ButtonLink } from "../ui/Button";
import { useToast } from "../ui/Toast";
import { useDocumentTitle } from "../../hooks/useDocumentTitle";
import { useLiveFeed } from "../../hooks/useLiveFeed";
import { routes } from "../../lib/env";
import type { ConnectionStatus } from "../../lib/realtime";
import { hostedEvents } from "../../lib/storage";
import { DangerZone } from "./DangerZone";
import { EventHeader } from "./EventHeader";
import { HostEventDeleted, HostEventNotFound, HostLoadError, HostLoading } from "./HostStatusScreens";
import { PhotosSection } from "./PhotosSection";
import { SaveHostLinkCallout } from "./SaveHostLinkCallout";
import { isUnauthorized, type HostSession } from "./session";
import { SettingsCard } from "./SettingsCard";
import { ShareCard } from "./ShareCard";
import { useEventControls } from "./useEventControls";
import { useTicker } from "./useTicker";

interface HostDashboardProps {
  eventId: string;
  token: string;
  /** The server has confirmed the token. The feed loads in parallel with that check. */
  verified: boolean;
  showSaveLink: boolean;
  onUnauthorized: () => void;
}

export function HostDashboard({ eventId, token, verified, showSaveLink, onUnauthorized }: HostDashboardProps) {
  const feed = useLiveFeed(eventId, { hostToken: token });
  const toast = useToast();
  const navigate = useNavigate();
  const titleRef = useRef<HTMLHeadingElement>(null);
  // Set while this host deletes the event: the live "event deleted" notice is
  // expected then, and must not flash the deleted screen before going home.
  const [deleting, setDeleting] = useState(false);
  useTicker(60_000);

  const reportError = useCallback(
    (error: unknown) => {
      if (isUnauthorized(error)) onUnauthorized();
      else toast.fromError(error);
    },
    [onUnauthorized, toast],
  );
  const session = useMemo<HostSession>(() => ({ eventId, token, reportError }), [eventId, token, reportError]);
  const { event, togglingUploads, setUploadsOpen, rename } = useEventControls(session, feed.event, feed.applyEvent);

  useDocumentTitle(event ? `${event.name} (host)` : "Host dashboard");

  // Keep this device's "Your events" list (home page) complete and up to date.
  const name = event?.name;
  const createdAt = event?.created_at;
  useEffect(() => {
    if (!verified || feed.gone || name === undefined || createdAt === undefined) return;
    const known = hostedEvents.list().find((item) => item.id === eventId);
    if (!known) hostedEvents.add({ id: eventId, name, createdAt });
    else if (known.name !== name) hostedEvents.rename(eventId, name);
  }, [verified, feed.gone, eventId, name, createdAt]);

  useEffect(() => {
    if (feed.gone) hostedEvents.remove(eventId);
  }, [feed.gone, eventId]);

  const onEventDeleted = () => {
    hostedEvents.remove(eventId);
    toast.success("Event deleted", event ? `“${event.name}” and its photos are gone.` : undefined);
    navigate(routes.home(), { replace: true });
  };

  if (feed.gone && !deleting) return feed.gone === "deleted" ? <HostEventDeleted /> : <HostEventNotFound />;
  // No event and no error yet: the first load (or a retry) is still running.
  if (!verified || (!event && !feed.error)) return <HostLoading />;
  if (!event) return <HostLoadError title="Couldn't load your event" error={feed.error} onRetry={feed.retry} />;

  return (
    <AppShell width="xl" headerRight={<HeaderActions eventId={eventId} status={feed.status} />}>
      <EventHeader event={event} titleRef={titleRef} />
      {showSaveLink && (
        <SaveHostLinkCallout eventId={eventId} token={token} event={event} onDismiss={() => titleRef.current?.focus()} />
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-3 lg:grid-rows-[auto_1fr] lg:items-start">
        <div className="min-w-0 space-y-6 lg:col-start-1 lg:row-start-1">
          <ShareCard event={event} />
          <SettingsCard
            event={event}
            togglingUploads={togglingUploads}
            onUploadsChange={(open) => void setUploadsOpen(open)}
            onRename={rename}
            onError={reportError}
          />
        </div>
        <PhotosSection
          className="lg:col-span-2 lg:col-start-2 lg:row-span-2 lg:row-start-1"
          session={session}
          photos={feed.photos}
          photoCount={event.photo_count}
          initialLoading={feed.initialLoading}
          hasMore={feed.hasMore}
          loadingMore={feed.loadingMore}
          loadMore={feed.loadMore}
          removePhoto={feed.removePhoto}
          latestLiveId={feed.latestLiveId}
        />
        <DangerZone
          className="min-w-0 lg:col-start-1 lg:row-start-2"
          session={session}
          eventName={event.name}
          photoCount={event.photo_count}
          onDeletingChange={setDeleting}
          onDeleted={onEventDeleted}
        />
      </div>
    </AppShell>
  );
}

function HeaderActions({ eventId, status }: { eventId: string; status: ConnectionStatus }) {
  return (
    <div className="flex items-center gap-2">
      <ConnectionBadge status={status} />
      <ButtonLink
        to={routes.wall(eventId)}
        target="_blank"
        rel="noreferrer"
        variant="secondary"
        className="max-sm:w-11 max-sm:px-0"
        icon={<MonitorPlay className="size-4" aria-hidden="true" />}
      >
        <span className="sr-only sm:not-sr-only">Open wall</span>
      </ButtonLink>
    </div>
  );
}
