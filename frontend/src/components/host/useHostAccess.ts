import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { useToast } from "../ui/Toast";
import { api } from "../../lib/api";
import { hostTokens, hostedEvents } from "../../lib/storage";
import { hasErrorCode, isUnauthorized, readHashToken } from "./session";

export type HostAccessStatus = "checking" | "ok" | "invalid" | "not_found" | "error";

export interface HostAccess {
  /** The host token for this event, or null when this device doesn't have one. */
  token: string | null;
  status: HostAccessStatus;
  /** Why verification failed when ``status`` is "error". */
  error: unknown;
  retry: () => void;
  /** The server rejected the token during a host action. */
  invalidate: () => void;
}

interface VerifyResult {
  token: string;
  attempt: number;
  status: Exclude<HostAccessStatus, "checking">;
  error: unknown;
}

/**
 * Finds the host token (a "#token=..." link or this device's storage), removes
 * it from the address bar and history right away, and checks it with the server.
 *
 * A link never costs the host a working token: when this device already has
 * one saved, the link's token is only saved once the server accepts it, and a
 * rejected link (e.g. cut off when copied) falls back to the saved one.
 */
export function useHostAccess(eventId: string): HostAccess {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();

  const hashToken = readHashToken(location.hash);
  const linkToken = hashToken !== null && hostTokens.isPlausible(hashToken) ? hashToken : null;
  const [token, setToken] = useState<string | null>(() => linkToken ?? hostTokens.get(eventId));
  // A different host link opened while this page is already showing. Compared
  // with the last link used (not ``token``), so falling back from a rejected
  // link sticks even if a render still sees the old address.
  const [lastLinkToken, setLastLinkToken] = useState(linkToken);
  if (linkToken !== null && linkToken !== lastLinkToken) {
    setLastLinkToken(linkToken);
    setToken(linkToken);
  }

  const { pathname, search, state } = location;
  useEffect(() => {
    if (hashToken === null) return;
    // Nothing to lose: save right away. Replacing a saved token waits for the server.
    if (hostTokens.isPlausible(hashToken) && hostTokens.get(eventId) === null) hostTokens.set(eventId, hashToken);
    navigate({ pathname, search }, { replace: true, state });
  }, [eventId, hashToken, navigate, pathname, search, state]);

  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<VerifyResult | null>(null);

  useEffect(() => {
    if (!token) return;
    let active = true;
    api.verifyHost(eventId, token).then(
      () => {
        hostTokens.set(eventId, token);
        if (active) setResult({ token, attempt, status: "ok", error: null });
      },
      (error: unknown) => {
        let status: VerifyResult["status"] = "error";
        if (isUnauthorized(error)) {
          const saved = hostTokens.get(eventId);
          if (saved !== null && saved !== token) {
            // A bad link: keep using the token this device already has.
            if (active) {
              setToken(saved);
              toast.info("That host link isn't valid", "Using the one saved on this device instead.");
            }
            return;
          }
          status = "invalid";
          if (saved === token) hostTokens.remove(eventId);
        } else if (hasErrorCode(error, "EVENT_NOT_FOUND")) {
          status = "not_found";
          hostedEvents.remove(eventId);
        }
        if (active) setResult({ token, attempt, status, error });
      },
    );
    return () => {
      active = false;
    };
  }, [eventId, token, attempt, toast]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  const invalidate = useCallback(() => {
    if (!token) return;
    if (hostTokens.get(eventId) === token) hostTokens.remove(eventId);
    setResult({ token, attempt, status: "invalid", error: null });
  }, [eventId, token, attempt]);

  const current = result && result.token === token && result.attempt === attempt ? result : null;
  return {
    token,
    status: current?.status ?? "checking",
    error: current?.error ?? null,
    retry,
    invalidate,
  };
}
