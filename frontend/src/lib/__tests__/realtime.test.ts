import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { wsUrl } from "../env";
import {
  CLOSE_EVENT_DELETED,
  CLOSE_EVENT_NOT_FOUND,
  CLOSE_RATE_LIMITED,
  CLOSE_TRY_AGAIN_LATER,
  type ConnectionStatus,
  type GoneReason,
  LiveSocket,
  type LiveSocketOptions,
  backoffDelay,
} from "../realtime";
import type { LiveMessage } from "../types";
import { EVENT_ID } from "./fixtures";

/** Minimal stand-in for the browser WebSocket, driven by the test. */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readonly protocols: string | string[] | undefined;
  readyState = 0;
  sent: string[] = [];
  closeCalls: { code?: number; reason?: string }[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string, protocols?: string | string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code?: number, reason?: string): void {
    this.closeCalls.push({ code, reason });
    this.readyState = 2;
  }

  // Test helpers: what the server/network would do.
  /** Accept the connection and greet, as the real server does. */
  serverOpen(): void {
    this.serverAccept();
    this.serverSend({ type: "hello", event_id: EVENT_ID, data: {} });
  }

  /** Accept without greeting (the server does this right before refusing a socket). */
  serverAccept(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  serverSend(data: unknown): void {
    const payload = typeof data === "string" ? data : JSON.stringify(data);
    this.onmessage?.({ data: payload } as MessageEvent);
  }

  serverClose(code: number): void {
    this.readyState = 3;
    this.onerror?.(new Event("error"));
    this.onclose?.({ code, reason: "", wasClean: false } as CloseEvent);
  }
}

const sockets = () => FakeWebSocket.instances;
function socketAt(index: number): FakeWebSocket {
  const socket = FakeWebSocket.instances[index];
  if (!socket) throw new Error(`No socket #${index} (have ${FakeWebSocket.instances.length})`);
  return socket;
}
const latest = () => socketAt(FakeWebSocket.instances.length - 1);

const started: LiveSocket[] = [];

function startLive(overrides: Partial<LiveSocketOptions> = {}) {
  const statuses: ConnectionStatus[] = [];
  const messages: LiveMessage[] = [];
  const onOpen = vi.fn<() => void>();
  const onGone = vi.fn<(reason: GoneReason) => void>();
  const live = new LiveSocket({
    eventId: EVENT_ID,
    onMessage: (message) => messages.push(message),
    onStatus: (status) => statuses.push(status),
    onOpen,
    onGone,
    WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
    random: () => 0.5, // no jitter: backoffDelay(n) = 1000 * 2^n
    ...overrides,
  });
  live.start();
  started.push(live);
  return { live, statuses, messages, onOpen, onGone };
}

function setOnline(value: boolean): void {
  vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(value);
}

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeWebSocket.instances = [];
});

afterEach(() => {
  for (const live of started.splice(0)) live.stop();
  // Drop the own-property override so the jsdom getter applies again.
  delete (document as unknown as Record<string, unknown>).visibilityState;
  vi.useRealTimers();
});

describe("backoffDelay", () => {
  it("starts at one second with ±30 % jitter", () => {
    expect(backoffDelay(0, () => 0)).toBe(700);
    expect(backoffDelay(0, () => 0.5)).toBe(1000);
    expect(backoffDelay(0, () => 0.999_999)).toBe(1300);
  });

  it("doubles with every attempt", () => {
    expect([1, 2, 3, 4].map((attempt) => backoffDelay(attempt, () => 0.5))).toEqual([2000, 4000, 8000, 16000]);
  });

  it("is capped at 30 seconds before jitter", () => {
    expect(backoffDelay(5, () => 0.5)).toBe(30_000);
    expect(backoffDelay(1000, () => 0.5)).toBe(30_000);
    expect(backoffDelay(1000, () => 0)).toBe(21_000);
    expect(backoffDelay(1000, () => 0.999_999)).toBe(39_000);
  });

  it("treats a negative attempt as the first one", () => {
    expect(backoffDelay(-3, () => 0.5)).toBe(1000);
  });

  it("never goes below the minimum", () => {
    expect(backoffDelay(0, () => 0, 10_000)).toBe(10_000);
    expect(backoffDelay(5, () => 0.5, 10_000)).toBe(30_000);
  });

  it("returns whole milliseconds inside the jitter bounds", () => {
    for (let index = 0; index <= 20; index += 1) {
      const random = index / 20.0001;
      const delay = backoffDelay(2, () => random);
      expect(Number.isInteger(delay)).toBe(true);
      expect(delay).toBeGreaterThanOrEqual(2800);
      expect(delay).toBeLessThanOrEqual(5200);
    }
  });

  it("uses Math.random by default", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    expect(backoffDelay(0)).toBe(700);
  });
});

describe("LiveSocket connecting", () => {
  it("connects to the event's socket with the v1 subprotocol", () => {
    startLive();
    expect(sockets()).toHaveLength(1);
    expect(latest().url).toBe(wsUrl(`/ws/events/${EVENT_ID}`));
    expect(latest().protocols).toEqual(["gambarkita.v1"]);
  });

  it("adds the host subprotocol when a host token is given", () => {
    const token = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFy";
    startLive({ hostToken: token });
    expect(latest().protocols).toEqual(["gambarkita.v1", `gambarkita.host.${token}`]);
    expect(latest().url).not.toContain(token);
  });

  it("omits the host subprotocol for a null host token", () => {
    startLive({ hostToken: null });
    expect(latest().protocols).toEqual(["gambarkita.v1"]);
  });

  it("URL-encodes the event id", () => {
    startLive({ eventId: "a/b c" });
    expect(latest().url).toBe(wsUrl("/ws/events/a%2Fb%20c"));
  });

  it("reports connecting, then open, and calls onOpen", () => {
    const { statuses, onOpen } = startLive();
    expect(statuses).toEqual(["connecting"]);
    expect(onOpen).not.toHaveBeenCalled();
    latest().serverOpen();
    expect(statuses).toEqual(["connecting", "open"]);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("ignores a second start()", () => {
    const { live } = startLive();
    live.start();
    expect(sockets()).toHaveLength(1);
  });

  it("retries when the WebSocket constructor throws", () => {
    let calls = 0;
    class Throwing extends FakeWebSocket {
      constructor(url: string, protocols?: string | string[]) {
        calls += 1;
        if (calls === 1) throw new SyntaxError("blocked");
        super(url, protocols);
      }
    }
    const { statuses } = startLive({ WebSocketImpl: Throwing as unknown as typeof WebSocket });
    expect(sockets()).toHaveLength(0);
    expect(statuses).toEqual(["connecting", "reconnecting"]);
    vi.advanceTimersByTime(1000);
    expect(sockets()).toHaveLength(1);
  });
});

describe("LiveSocket messages", () => {
  it("passes parsed messages on", () => {
    const { messages } = startLive();
    latest().serverOpen();
    latest().serverSend({ type: "photo.deleted", event_id: EVENT_ID, data: { id: "p1" } });
    latest().serverSend({ type: "pong" });
    expect(messages).toEqual([
      { type: "hello", event_id: EVENT_ID, data: {} },
      { type: "photo.deleted", event_id: EVENT_ID, data: { id: "p1" } },
      { type: "pong" },
    ]);
  });

  it("ignores messages that are not JSON objects with a type", () => {
    const { messages } = startLive();
    latest().serverAccept();
    for (const payload of ["not json", "{broken", "null", "42", '"hello"', '{"data":1}', '{"type":5}']) {
      latest().serverSend(payload);
    }
    expect(messages).toEqual([]);
  });

  it("ignores messages from a socket it has already replaced", () => {
    const { messages } = startLive();
    const first = latest();
    first.serverAccept();
    vi.advanceTimersByTime(75_000); // stale: replaced
    vi.advanceTimersByTime(1000);
    expect(sockets()).toHaveLength(2);
    first.serverSend({ type: "pong" });
    expect(messages).toEqual([]);
  });
});

describe("LiveSocket reconnecting", () => {
  it("reconnects with growing backoff and resets it after a successful open", () => {
    const { statuses, onOpen } = startLive();
    socketAt(0).serverOpen();
    socketAt(0).serverClose(1006);
    expect(statuses).toEqual(["connecting", "open", "reconnecting"]);

    vi.advanceTimersByTime(999);
    expect(sockets()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(2);

    socketAt(1).serverClose(1006); // failed before opening: next wait doubles
    vi.advanceTimersByTime(1999);
    expect(sockets()).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(3);

    socketAt(2).serverOpen();
    expect(onOpen).toHaveBeenCalledTimes(2);
    socketAt(2).serverClose(1006);
    vi.advanceTimersByTime(1000); // back to the first delay
    expect(sockets()).toHaveLength(4);
    socketAt(3).serverOpen();

    expect(onOpen).toHaveBeenCalledTimes(3);
    expect(statuses).toEqual(["connecting", "open", "reconnecting", "open", "reconnecting", "open"]);
  });

  it.each([
    [CLOSE_EVENT_NOT_FOUND, "not_found"],
    [CLOSE_EVENT_DELETED, "deleted"],
  ] as const)("stops for good on close code %i (%s)", (code, reason) => {
    const { statuses, onGone } = startLive();
    latest().serverOpen();
    latest().serverClose(code);
    expect(onGone).toHaveBeenCalledWith(reason);
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(statuses.at(-1)).toBe("closed");

    vi.advanceTimersByTime(10 * 60_000);
    window.dispatchEvent(new Event("online"));
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(sockets()).toHaveLength(1);
  });

  it.each([CLOSE_RATE_LIMITED, CLOSE_TRY_AGAIN_LATER])("waits 10 seconds plus backoff after close code %i", (code) => {
    const { statuses } = startLive();
    latest().serverOpen();
    latest().serverClose(code);
    expect(statuses.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(10_999); // 10 s minimum + 1 s backoff (attempt 0, no jitter)
    expect(sockets()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(2);
  });

  it("does not let tab switches or 'online' skip the server's back-off", () => {
    startLive();
    latest().serverOpen();
    latest().serverClose(CLOSE_RATE_LIMITED);
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(1);
    vi.advanceTimersByTime(11_000);
    expect(sockets()).toHaveLength(2);
  });

  it("does not treat an accept-then-refuse as a successful connection", () => {
    const { statuses, onOpen } = startLive();
    latest().serverAccept(); // the server accepts only to deliver the close code
    latest().serverClose(CLOSE_RATE_LIMITED);
    expect(onOpen).not.toHaveBeenCalled();
    expect(statuses).not.toContain("open");
    vi.advanceTimersByTime(11_000);
    latest().serverAccept();
    latest().serverClose(CLOSE_RATE_LIMITED);
    // attempt was not reset by the bare 'open': the next wait grows (10 s + 2 s)
    vi.advanceTimersByTime(11_999);
    expect(sockets()).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(3);
  });

  it("uses the injected random source for jitter", () => {
    startLive({ random: () => 0 });
    latest().serverClose(1006);
    vi.advanceTimersByTime(699);
    expect(sockets()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(2);
  });
});

describe("LiveSocket heartbeat", () => {
  it("sends a ping every 25 seconds once open", () => {
    startLive();
    vi.advanceTimersByTime(60_000);
    expect(latest().sent).toEqual([]); // not open yet

    latest().serverOpen();
    vi.advanceTimersByTime(24_999);
    expect(latest().sent).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(latest().sent.map((data) => JSON.parse(data) as unknown)).toEqual([{ type: "ping" }]);
    vi.advanceTimersByTime(25_000);
    expect(latest().sent).toHaveLength(2);
  });

  it("keeps a connection that keeps receiving messages", () => {
    startLive();
    latest().serverOpen();
    for (let second = 0; second < 300; second += 20) {
      vi.advanceTimersByTime(20_000);
      latest().serverSend({ type: "pong" });
    }
    expect(sockets()).toHaveLength(1);
    expect(latest().closeCalls).toEqual([]);
  });

  it("drops a connection that has been silent for more than 70 seconds and reconnects", () => {
    const { statuses, onOpen } = startLive();
    const first = latest();
    first.serverOpen();

    vi.advanceTimersByTime(50_000); // two pings, still considered alive
    expect(first.closeCalls).toEqual([]);
    expect(first.sent).toHaveLength(2);

    vi.advanceTimersByTime(25_000); // 75 s of silence
    expect(first.closeCalls).toEqual([{ code: 4000, reason: "Stale connection" }]);
    expect(first.onclose).toBeNull(); // a late close event must not schedule a second reconnect
    expect(statuses.at(-1)).toBe("reconnecting");

    vi.advanceTimersByTime(1000);
    expect(sockets()).toHaveLength(2);
    latest().serverOpen();
    expect(onOpen).toHaveBeenCalledTimes(2);
    expect(first.sent).toHaveLength(2); // the old socket's heartbeat stopped
  });

  it("stops the heartbeat when the socket closes", () => {
    startLive();
    const first = latest();
    first.serverOpen();
    first.serverClose(1006);
    vi.advanceTimersByTime(100_000);
    expect(first.sent).toEqual([]);
  });
});

describe("LiveSocket stop", () => {
  it("closes the socket, reports closed and never reconnects", () => {
    const { live, statuses } = startLive();
    const socket = latest();
    socket.serverOpen();
    live.stop();

    expect(socket.closeCalls).toEqual([{ code: 1000, reason: "Client closed" }]);
    expect(statuses.at(-1)).toBe("closed");

    socket.serverClose(1000); // the browser's close event after our close()
    vi.advanceTimersByTime(10 * 60_000);
    window.dispatchEvent(new Event("online"));
    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(sockets()).toHaveLength(1);
    expect(socket.sent).toEqual([]);
  });

  it("closes a socket that is still connecting", () => {
    const { live } = startLive();
    live.stop();
    expect(latest().closeCalls).toEqual([{ code: 1000, reason: "Client closed" }]);
  });

  it("cancels a pending reconnect", () => {
    const { live } = startLive();
    latest().serverClose(1006);
    live.stop();
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toHaveLength(1);
  });

  it("can be started again after stopping", () => {
    const { live, statuses } = startLive();
    live.stop();
    live.start();
    expect(sockets()).toHaveLength(2);
    expect(statuses).toEqual(["connecting", "closed", "connecting"]);
  });
});

describe("LiveSocket network and visibility", () => {
  it("reconnects immediately when the device comes back online", () => {
    const { statuses } = startLive();
    latest().serverOpen();
    latest().serverClose(1006);
    expect(sockets()).toHaveLength(1);

    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(2);
    expect(statuses.at(-1)).toBe("connecting");

    vi.advanceTimersByTime(60_000); // the pending backoff timer was cancelled
    expect(sockets()).toHaveLength(2);
  });

  it("does not open a second socket when online fires while connected", () => {
    startLive();
    latest().serverOpen();
    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(1);
  });

  it("reconnects immediately when the tab becomes visible again", () => {
    startLive();
    latest().serverClose(1006);

    setVisibility("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(sockets()).toHaveLength(1);

    setVisibility("visible");
    document.dispatchEvent(new Event("visibilitychange"));
    expect(sockets()).toHaveLength(2);
  });

  it("reports offline and waits for the network when started offline", () => {
    setOnline(false);
    const { statuses } = startLive();
    expect(statuses).toEqual(["offline"]);
    expect(sockets()).toHaveLength(0);

    vi.advanceTimersByTime(60_000);
    expect(sockets()).toHaveLength(0);

    setOnline(true);
    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(1);
    expect(statuses).toEqual(["offline", "connecting"]);
  });

  it("reports offline when the connection drops while the device is offline", () => {
    const { statuses } = startLive();
    latest().serverOpen();
    setOnline(false);
    window.dispatchEvent(new Event("offline"));
    expect(statuses.at(-1)).toBe("open"); // the socket itself is still up

    latest().serverClose(1006);
    expect(statuses.at(-1)).toBe("offline");
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toHaveLength(1);
  });

  it("reports offline when the offline event fires between attempts", () => {
    const { statuses } = startLive();
    latest().serverClose(1006);
    expect(statuses.at(-1)).toBe("reconnecting");
    window.dispatchEvent(new Event("offline"));
    expect(statuses.at(-1)).toBe("offline");
  });

  it("removes its listeners on stop", () => {
    const removeWindow = vi.spyOn(window, "removeEventListener");
    const removeDocument = vi.spyOn(document, "removeEventListener");
    const { live } = startLive();
    live.stop();
    const windowEvents = removeWindow.mock.calls.map(([type]) => type);
    expect(windowEvents).toEqual(expect.arrayContaining(["online", "offline"]));
    expect(removeDocument.mock.calls.map(([type]) => type)).toContain("visibilitychange");
  });
});
