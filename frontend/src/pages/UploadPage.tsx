import { Images } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "../components/AppShell";
import { ButtonLink } from "../components/ui/Button";
import { LoadingScreen } from "../components/ui/StatusScreen";
import { useToast } from "../components/ui/Toast";
import { EventLoadErrorScreen, EventNotFoundScreen, UploadsClosedScreen } from "../components/upload/UploadStatusScreens";
import { UploadWorkspace } from "../components/upload/UploadWorkspace";
import { type EventDetailsState, useEventDetails } from "../components/upload/useEventDetails";
import { useGuestQuota } from "../components/upload/useGuestQuota";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { usePublicConfig } from "../hooks/usePublicConfig";
import type { ApiError } from "../lib/api";
import { routes } from "../lib/env";
import { guestLimitOf } from "../lib/errors";
import type { EventPublic } from "../lib/types";

type Screen = "loading" | "not_found" | "error" | "closed" | "full" | "guest_limit" | "open";

const UNSENT = "Photos that hadn't finished sending weren't added.";
const CHECK_BACK = "Please check back a little later.";

/** Guest page behind the QR code: add photos to the event's live wall. */
export default function UploadPage({ eventId }: { eventId: string }) {
  const toast = useToast();
  const { config, loaded: configLoaded } = usePublicConfig();
  const { state, reload, refresh, update, markGone } = useEventDetails(eventId);
  const [sharedCount, setSharedCount] = useState(0);
  const [checking, setChecking] = useState(false);
  /** The server turned a photo away: the event has reached its photo limit. */
  const [limitReached, setLimitReached] = useState(false);
  const {
    recordUpload,
    markReached,
    refresh: refreshAllowance,
    pending: allowancePending,
    ...allowance
  } = useGuestQuota(
    eventId,
    configLoaded ? config.max_photos_per_guest : null,
  );
  const screenRef = useRef<HTMLDivElement>(null);
  const shownScreen = useRef<Screen | null>(null);

  // Only judged against the server's real limit, not the defaults used until /api/config answers.
  const isFull = (event: EventPublic) => configLoaded && event.photo_count >= config.max_photos_per_event;

  useDocumentTitle(
    state.status === "ready" ? state.event.name : state.status === "not_found" ? "Event not found" : null,
  );

  const handleUploaded = useCallback(() => {
    setSharedCount((count) => count + 1);
    update((event) => ({ ...event, photo_count: event.photo_count + 1 }));
    recordUpload();
  }, [update, recordUpload]);

  const handleStopped = useCallback(
    (error: ApiError) => {
      switch (error.code) {
        case "UPLOADS_CLOSED":
          toast.info("The host has closed uploads", UNSENT);
          update((event) => ({ ...event, uploads_open: false }));
          break;
        case "EVENT_PHOTO_LIMIT":
          toast.info("This event is full", UNSENT);
          setLimitReached(true);
          break;
        case "GUEST_PHOTO_LIMIT":
          toast.info("You've shared all your photos", UNSENT);
          markReached(guestLimitOf(error));
          break;
        case "EVENT_NOT_FOUND":
          toast.error("This event is no longer available");
          markGone();
          break;
        default:
          toast.fromError(error);
      }
    },
    [update, markGone, markReached, toast],
  );

  const checkAgain = async (variant: "closed" | "full" | "guest_limit") => {
    setChecking(true);
    try {
      const [event, quota] = await Promise.all([
        refresh(),
        variant === "guest_limit" ? refreshAllowance() : Promise.resolve(null),
      ]);
      // The host may have made room by deleting photos: the fresh counts decide from here.
      setLimitReached(false);
      if (!event) return;
      if (!event.uploads_open) toast.info("Uploads are still closed", CHECK_BACK);
      else if (isFull(event)) toast.info("This event is still full", CHECK_BACK);
      else if (quota?.remaining === 0) {
        toast.info("You've still shared all your photos", "If the host removes one of yours, you can share another.");
      }
    } catch (error) {
      toast.fromError(error);
    } finally {
      setChecking(false);
    }
  };

  // A returning guest's count decides which screen to show: wait for it rather than flash the form.
  const details: EventDetailsState = allowancePending && state.status === "ready" ? { status: "loading" } : state;
  let screen: Screen;
  let content: ReactNode;
  switch (details.status) {
    case "loading":
      screen = "loading";
      content = <LoadingScreen label="Loading event…" />;
      break;
    case "not_found":
      screen = "not_found";
      content = <EventNotFoundScreen />;
      break;
    case "error":
      screen = "error";
      content = <EventLoadErrorScreen error={details.error} onRetry={reload} />;
      break;
    case "ready": {
      const { event } = details;
      const variant = !event.uploads_open
        ? "closed"
        : limitReached || isFull(event)
          ? "full"
          : allowance.remaining === 0
            ? "guest_limit"
            : null;
      screen = variant ?? "open";
      content = variant ? (
        <UploadsClosedScreen
          variant={variant}
          eventId={eventId}
          eventName={event.name}
          sharedCount={sharedCount}
          guestLimit={allowance.limit}
          checking={checking}
          onCheckAgain={() => void checkAgain(variant)}
        />
      ) : (
        <UploadWorkspace
          event={event}
          sharedCount={sharedCount}
          allowance={allowance}
          onUploaded={handleUploaded}
          onStopped={handleStopped}
        />
      );
      break;
    }
  }

  // When one screen replaces another the guest was using (uploads closed or the event deleted
  // mid-upload, "Check again"...), its heading takes focus: the change is announced, and
  // keyboard focus isn't lost with the controls that went away.
  useEffect(() => {
    const previous = shownScreen.current;
    shownScreen.current = screen;
    if (previous === null || previous === "loading" || previous === screen) return;
    const heading = screenRef.current?.querySelector("h1");
    if (!heading) return;
    heading.tabIndex = -1;
    heading.focus();
  }, [screen]);

  return (
    <AppShell
      width="sm"
      homeLink={false}
      headerRight={
        state.status !== "not_found" && (
          <ButtonLink
            to={routes.wall(eventId)}
            variant="ghost"
            icon={<Images className="size-4" aria-hidden="true" />}
          >
            Wall
          </ButtonLink>
        )
      }
    >
      <div ref={screenRef} className="[&_h1:focus]:outline-none">
        {content}
      </div>
    </AppShell>
  );
}
