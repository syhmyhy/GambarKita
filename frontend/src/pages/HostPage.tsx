import { useState } from "react";
import { useLocation } from "react-router";

import { HostDashboard } from "../components/host/HostDashboard";
import {
  HostAccessNeeded,
  HostEventNotFound,
  HostLoadError,
  InvalidHostLink,
} from "../components/host/HostStatusScreens";
import { useHostAccess } from "../components/host/useHostAccess";
import { hostedEvents } from "../lib/storage";

function wasJustCreated(state: unknown): boolean {
  return typeof state === "object" && state !== null && (state as { justCreated?: unknown }).justCreated === true;
}

export default function HostPage({ eventId }: { eventId: string }) {
  const access = useHostAccess(eventId);
  const location = useLocation();
  // Decided once, before the dashboard records the event on this device.
  const [showSaveLink] = useState(
    () => wasJustCreated(location.state) || !hostedEvents.list().some((item) => item.id === eventId),
  );

  if (!access.token) return <HostAccessNeeded eventId={eventId} />;

  switch (access.status) {
    case "invalid":
      return <InvalidHostLink eventId={eventId} />;
    case "not_found":
      return <HostEventNotFound />;
    case "error":
      return <HostLoadError title="Couldn't check your host link" error={access.error} onRetry={access.retry} />;
    default:
      return (
        <HostDashboard
          eventId={eventId}
          token={access.token}
          verified={access.status === "ok"}
          showSaveLink={showSaveLink}
          onUnauthorized={access.invalidate}
        />
      );
  }
}
