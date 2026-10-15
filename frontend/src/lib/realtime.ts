import { wsUrl } from "./env";
import type { LiveMessage } from "./types";

export type ConnectionStatus = "connecting" | "open" | "reconnecting" | "offline" | "closed";
export type GoneReason = "not_found" | "deleted";

const SUBPROTOCOL = "gambarkita.v1";
const HOST_SUBPROTOCOL_PREFIX = "gambarkita.host.";

// Server close codes (see backend app/api/routes/realtime.py).
export const CLOSE_EVENT_NOT_FOUND = 4404;
export const CLOSE_EVENT_DELETED = 4410;
export const CLOSE_RATE_LIMITED = 4429;
export const CLOSE_TRY_AGAIN_LATER = 1013;

const HEARTBEAT_INTERVAL = 25_000;
const STALE_AFTER = 70_000; // no message at all for this long: the socket is dead
const BASE_DELAY = 1_000;
const MAX_DELAY = 30_000;
const THROTTLED_MIN_DELAY = 10_000;

export interface LiveSocketOptions {
  eventId: string;
  /** Lets the host's wall use the connection slots reserved for hosts. */
  hostToken?: string | null;
  onMessage: (message: LiveMessage) => void;
  onStatus?: (status: ConnectionStatus) => void;
  /** Called on every successful (re)connect: resynchronise state here. */
  onOpen?: () => void;
  /** The event is gone; the socket stops for good. */
  onGone?: (reason: GoneReason) => void;
  WebSocketImpl?: typeof WebSocket;
  random?: () => number;
}

/** Exponential backoff with ±30 % jitter. */
export function backoffDelay(attempt: number, random: () => number = Math.random, minimum = 0): number {
  const exponential = Math.min(MAX_DELAY, BASE_DELAY * 2 ** Math.max(0, attempt));
  const jittered = exponential * (0.7 + random() * 0.6);
  return Math.round(Math.max(minimum, jittered));
}

/**
 * A self-healing WebSocket subscription to one event's live updates:
 * reconnects with backoff, sends heartbeats, detects dead connections and
 * reconnects immediately when the device comes back online or the tab
 * becomes visible again (unless the server asked us to back off).
 *
 * The connection only counts as open once the server's "hello" arrives: the
 * server accepts and then immediately closes sockets it refuses (rate or
 * capacity limits), so a bare `open` event proves nothing.
 */
export class LiveSocket {
  private readonly options: LiveSocketOptions;
  private socket: WebSocket | null = null;
  private status: ConnectionStatus = "closed";
  private attempt = 0;
  private stopped = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private heartbeatTimer: ReturnType<typeof setInterval> | undefined;
  private lastMessageAt = 0;
  /** Do not reconnect before this time (the server said "try again later"). */
  private throttledUntil = 0;

  constructor(options: LiveSocketOptions) {
    this.options = options;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    window.addEventListener("online", this.handleOnline);
    window.addEventListener("offline", this.handleOffline);
    document.addEventListener("visibilitychange", this.handleVisibility);
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    window.removeEventListener("online", this.handleOnline);
    window.removeEventListener("offline", this.handleOffline);
    document.removeEventListener("visibilitychange", this.handleVisibility);
    clearTimeout(this.reconnectTimer);
    this.stopHeartbeat();
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState <= 1) socket.close(1000, "Client closed");
    this.setStatus("closed");
  }

  private connect(): void {
    if (this.stopped) return;
    clearTimeout(this.reconnectTimer);
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      this.setStatus("offline");
      return;
    }
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    const protocols = [SUBPROTOCOL];
    if (this.options.hostToken) protocols.push(`${HOST_SUBPROTOCOL_PREFIX}${this.options.hostToken}`);
    const Impl = this.options.WebSocketImpl ?? WebSocket;
    let socket: WebSocket;
    try {
      socket = new Impl(wsUrl(`/ws/events/${encodeURIComponent(this.options.eventId)}`), protocols);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      if (socket !== this.socket) return;
      this.lastMessageAt = Date.now();
      this.startHeartbeat();
    };
    socket.onmessage = (event: MessageEvent) => {
      if (socket !== this.socket) return;
      this.lastMessageAt = Date.now();
      let message: LiveMessage;
      try {
        message = JSON.parse(String(event.data)) as LiveMessage;
      } catch {
        return;
      }
      if (!message || typeof message !== "object" || typeof message.type !== "string") return;
      if (message.type === "hello" && this.status !== "open") {
        this.attempt = 0;
        this.throttledUntil = 0;
        this.setStatus("open");
        this.options.onMessage(message);
        this.options.onOpen?.();
        return;
      }
      this.options.onMessage(message);
    };
    socket.onclose = (event: CloseEvent) => {
      if (socket !== this.socket) return;
      this.socket = null;
      this.stopHeartbeat();
      if (this.stopped) return;
      if (event.code === CLOSE_EVENT_NOT_FOUND || event.code === CLOSE_EVENT_DELETED) {
        this.stopped = true;
        this.setStatus("closed");
        this.options.onGone?.(event.code === CLOSE_EVENT_NOT_FOUND ? "not_found" : "deleted");
        return;
      }
      const throttled = event.code === CLOSE_RATE_LIMITED || event.code === CLOSE_TRY_AGAIN_LATER;
      if (throttled) this.throttledUntil = Date.now() + THROTTLED_MIN_DELAY;
      this.scheduleReconnect(throttled ? THROTTLED_MIN_DELAY : 0);
    };
    // onerror is always followed by onclose, which handles reconnecting.
    socket.onerror = () => undefined;
  }

  private scheduleReconnect(minimum = 0): void {
    if (this.stopped) return;
    // Throttled retries still back off (and jitter) on top of the minimum, so
    // a crowd sharing one IP does not reconnect in lockstep every 10 s.
    const delay = minimum + backoffDelay(this.attempt, this.options.random);
    this.attempt += 1;
    this.setStatus(typeof navigator !== "undefined" && navigator.onLine === false ? "offline" : "reconnecting");
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private reconnectNow(): void {
    if (this.stopped || this.socket || Date.now() < this.throttledUntil) return;
    this.attempt = 0;
    this.connect();
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      const socket = this.socket;
      if (!socket || socket.readyState !== 1) return;
      if (Date.now() - this.lastMessageAt > STALE_AFTER) {
        // Half-open connection (e.g. the phone switched networks): start over.
        this.socket = null;
        socket.onclose = null;
        socket.close(4000, "Stale connection");
        this.stopHeartbeat();
        this.scheduleReconnect();
        return;
      }
      socket.send(JSON.stringify({ type: "ping" }));
    }, HEARTBEAT_INTERVAL);
  }

  private stopHeartbeat(): void {
    clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = undefined;
  }

  private setStatus(status: ConnectionStatus): void {
    if (status === this.status) return;
    this.status = status;
    this.options.onStatus?.(status);
  }

  private readonly handleOnline = (): void => this.reconnectNow();

  private readonly handleOffline = (): void => {
    if (!this.socket) this.setStatus("offline");
  };

  private readonly handleVisibility = (): void => {
    if (document.visibilityState === "visible") this.reconnectNow();
  };
}
