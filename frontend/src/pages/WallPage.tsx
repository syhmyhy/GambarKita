import { CalendarX2, PartyPopper, RefreshCw, WifiOff } from "lucide-react";
import { useState } from "react";

import { Button } from "../components/ui/Button";
import { LiveWall } from "../components/wall/LiveWall";
import { useDarkPageChrome } from "../components/wall/usePageChrome";
import { WallLoading, WallStatus } from "../components/wall/WallStatus";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLiveFeed } from "../hooks/useLiveFeed";
import { useWakeLock } from "../hooks/useWakeLock";
import { describeError } from "../lib/errors";
import { hostTokens } from "../lib/storage";

/** The live photo wall, for a TV or projector at the venue and for guests' phones. */
export default function WallPage({ eventId }: { eventId: string }) {
  // A host's own screen gets the connection slots reserved for hosts.
  const [hostToken] = useState(() => hostTokens.get(eventId));
  const feed = useLiveFeed(eventId, { hostToken });
  useWakeLock(true);
  useDarkPageChrome();
  useDocumentTitle(feed.event ? `${feed.event.name} · Live wall` : "Live wall");

  // No "Go to home" button: guests open the wall too, and the home page is for hosts.
  if (feed.gone === "not_found") {
    return (
      <WallStatus
        icon={CalendarX2}
        title="Event not found"
        message="Check the link or scan the event's QR code again."
      />
    );
  }
  if (feed.gone === "deleted") {
    return (
      <WallStatus
        icon={PartyPopper}
        title="This event has ended"
        message="The host has closed this event, so its photos are no longer shown here. Thanks for celebrating!"
      />
    );
  }
  if (feed.initialLoading) return <WallLoading />;
  if (!feed.event) {
    if (!feed.error) return <WallLoading />;
    const friendly = describeError(feed.error);
    return (
      <WallStatus
        icon={WifiOff}
        tone="danger"
        title={friendly.title}
        message={friendly.message}
        actions={
          <Button variant="light" icon={<RefreshCw className="size-4" aria-hidden="true" />} onClick={feed.retry}>
            Try again
          </Button>
        }
      />
    );
  }

  return <LiveWall eventId={eventId} event={feed.event} feed={feed} />;
}
