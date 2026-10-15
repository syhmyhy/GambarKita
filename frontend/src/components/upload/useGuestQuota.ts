import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../lib/api";
import { guestKeys } from "../../lib/storage";
import type { GuestQuota } from "../../lib/types";

export interface GuestAllowance {
  /** Photos each guest may share, or null: no limit, or not known. */
  limit: number | null;
  /** Photos this guest can still share, or null whenever that isn't known. */
  remaining: number | null;
  /** Still asking the server how many photos a returning guest has shared. */
  pending: boolean;
  /** A photo of this guest's made it to the wall. */
  recordUpload: () => void;
  /** The server turned a photo away: this guest is at the limit (``limit`` if it said). */
  markReached: (limit: number | null) => void;
  /** Ask the server again: the host may have deleted some of this guest's photos, or changed the limit. */
  refresh: () => Promise<GuestQuota>;
}

type ServerCount =
  | { status: "none" }
  | { status: "loading" }
  | { status: "failed" }
  /** ``baseline``: photos counted locally when the server answered (its count already includes them). */
  | { status: "loaded"; limit: number | null; used: number; baseline: number };

/**
 * This guest's allowance when the server limits photos per guest.
 *
 * A browser without a saved guest key for the event hasn't shared anything here,
 * so it starts from the configured limit without asking. A returning guest's
 * count is fetched at once, alongside the event, so the page can show the right
 * screen straight away. If that fails, nothing is shown: the server still
 * enforces the limit, and its refusal switches to the "all shared" screen.
 */
export function useGuestQuota(eventId: string, configLimit: number | null): GuestAllowance {
  const [returning] = useState(() => guestKeys.has(eventId));
  const [server, setServer] = useState<ServerCount>(() => (returning ? { status: "loading" } : { status: "none" }));
  /**
   * Photos this guest shared while the page was open. One may land while the
   * count is loading; the server's count includes it, hence the baseline.
   */
  const [uploaded, setUploaded] = useState(0);
  const uploadedRef = useRef(0);
  const [reached, setReached] = useState<{ limit: number | null } | null>(null);

  useEffect(() => {
    if (!returning) return;
    const controller = new AbortController();
    api.getGuestQuota(eventId, controller.signal).then(
      ({ limit, used }) => setServer({ status: "loaded", limit, used, baseline: uploadedRef.current }),
      () => {
        if (!controller.signal.aborted) setServer({ status: "failed" });
      },
    );
    return () => controller.abort();
  }, [eventId, returning]);

  const recordUpload = useCallback(() => {
    uploadedRef.current += 1;
    setUploaded(uploadedRef.current);
  }, []);
  const markReached = useCallback((limit: number | null) => setReached({ limit }), []);

  const refresh = useCallback(async () => {
    const quota = await api.getGuestQuota(eventId);
    setServer({ status: "loaded", limit: quota.limit, used: quota.used, baseline: uploadedRef.current });
    setReached(null);
    return quota;
  }, [eventId]);

  let limit: number | null = null;
  let used: number | null = null;
  if (server.status === "loaded") {
    limit = server.limit;
    used = server.used + uploaded - server.baseline;
  } else if (server.status === "none" && configLimit !== null) {
    limit = configLimit;
    used = uploaded;
  }

  if (reached) {
    return { limit: reached.limit ?? limit, remaining: 0, pending: false, recordUpload, markReached, refresh };
  }
  return {
    limit,
    remaining: limit === null || used === null ? null : Math.max(0, limit - used),
    pending: server.status === "loading",
    recordUpload,
    markReached,
    refresh,
  };
}
