import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";
import type { EventPublic } from "../../lib/types";

export type EventDetailsState =
  | { status: "loading" }
  | { status: "ready"; event: EventPublic }
  | { status: "not_found" }
  | { status: "error"; error: unknown };

export interface EventDetails {
  state: EventDetailsState;
  /** Show the loading screen and fetch again (after a failed load). */
  reload: () => void;
  /** Fetch again in the background. Resolves with the event, or null once it's gone; other errors reject. */
  refresh: () => Promise<EventPublic | null>;
  /** Apply a local change to the loaded event. */
  update: (change: (event: EventPublic) => EventPublic) => void;
  markGone: () => void;
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.code === "EVENT_NOT_FOUND";
}

export function useEventDetails(eventId: string): EventDetails {
  const [state, setState] = useState<EventDetailsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.getEvent(eventId, controller.signal).then(
      (event) => setState({ status: "ready", event }),
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState(isNotFound(error) ? { status: "not_found" } : { status: "error", error });
      },
    );
    return () => controller.abort();
  }, [eventId, attempt]);

  const reload = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const event = await api.getEvent(eventId);
      setState({ status: "ready", event });
      return event;
    } catch (error) {
      if (!isNotFound(error)) throw error;
      setState({ status: "not_found" });
      return null;
    }
  }, [eventId]);

  const update = useCallback((change: (event: EventPublic) => EventPublic) => {
    setState((current) => (current.status === "ready" ? { status: "ready", event: change(current.event) } : current));
  }, []);

  const markGone = useCallback(() => setState({ status: "not_found" }), []);

  return { state, reload, refresh, update, markGone };
}
