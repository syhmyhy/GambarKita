import { useRef, useState } from "react";

import { useToast } from "../ui/Toast";
import { api } from "../../lib/api";
import { hostedEvents } from "../../lib/storage";
import type { EventPublic } from "../../lib/types";
import type { HostSession } from "./session";

export interface EventControls {
  /** The live event, showing an uploads change that is still being saved. */
  event: EventPublic | null;
  togglingUploads: boolean;
  setUploadsOpen: (open: boolean) => Promise<void>;
  /** Resolves with the saved name; rejects so the form can show validation errors. */
  rename: (name: string) => Promise<string>;
}

/**
 * The host's changes to the event. Saved changes go straight into the live
 * feed (``applyEvent``), so they show even when the live connection is down.
 */
export function useEventControls(
  session: HostSession,
  serverEvent: EventPublic | null,
  applyEvent: (event: EventPublic) => void,
): EventControls {
  const toast = useToast();
  // Shown optimistically while saving; dropping it rolls back to the server's value.
  const [pendingUploads, setPendingUploads] = useState<boolean | null>(null);
  const toggling = useRef(false);

  const event = serverEvent && pendingUploads !== null ? { ...serverEvent, uploads_open: pendingUploads } : serverEvent;

  const setUploadsOpen = async (open: boolean) => {
    if (toggling.current) return;
    toggling.current = true;
    setPendingUploads(open);
    try {
      applyEvent(await api.updateEvent(session.eventId, session.token, { uploads_open: open }));
      if (open) toast.success("Uploads are open", "Guests can add photos again.");
      else toast.success("Uploads closed", "Guests can still watch the wall but can't add new photos.");
    } catch (error) {
      session.reportError(error);
    } finally {
      toggling.current = false;
      setPendingUploads(null);
    }
  };

  const rename = async (next: string) => {
    const updated = await api.updateEvent(session.eventId, session.token, { name: next });
    applyEvent(updated);
    hostedEvents.rename(session.eventId, updated.name);
    toast.success("Event renamed");
    return updated.name;
  };

  return { event, togglingUploads: pendingUploads !== null, setUploadsOpen, rename };
}
