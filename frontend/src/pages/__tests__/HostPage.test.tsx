import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UNDO_DELETE_MS } from "../../components/host/PhotosSection";
import { ToastProvider } from "../../components/ui/Toast";
import { type LiveFeed, useLiveFeed } from "../../hooks/useLiveFeed";
import { ApiError, api, networkError } from "../../lib/api";
import { hostTokens, hostedEvents } from "../../lib/storage";
import type { EventPublic, Photo, PublicConfig } from "../../lib/types";
import HostPage from "../HostPage";

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return {
    ...actual,
    api: {
      getConfig: vi.fn(),
      createEvent: vi.fn(),
      getEvent: vi.fn(),
      listPhotos: vi.fn(),
      updateEvent: vi.fn(),
      deleteEvent: vi.fn(),
      verifyHost: vi.fn(),
      deletePhoto: vi.fn(),
    },
  };
});
vi.mock("../../hooks/useLiveFeed");

const EVENT_ID = "k3x9p2m7q1ab";
const TOKEN = "Zx9_-" + "a1B2c3D4e5".repeat(4);
const HOST_PATH = `/e/${EVENT_ID}/host`;

const CONFIG: PublicConfig = {
  max_upload_bytes: 10 * 1024 * 1024,
  allowed_mime_types: ["image/jpeg", "image/png", "image/webp"],
  max_photos_per_event: 2000,
  max_photos_per_guest: null,
  max_event_name_length: 80,
  max_uploader_name_length: 40,
  max_caption_length: 140,
  event_creation_requires_key: false,
};

const EVENT: EventPublic = {
  id: EVENT_ID,
  name: "Aisyah & Daniel's Wedding",
  uploads_open: true,
  photo_count: 2,
  created_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
  links: { upload: `http://localhost:3000/e/${EVENT_ID}`, wall: `http://localhost:3000/e/${EVENT_ID}/wall` },
};

function makePhoto(id: string, uploader: string | null, caption: string | null = null): Photo {
  return {
    id,
    event_id: EVENT_ID,
    url: `/media/events/${EVENT_ID}/photos/${id}.jpg`,
    thumbnail_url: `/media/events/${EVENT_ID}/thumbs/${id}.webp`,
    content_type: "image/jpeg",
    width: 1600,
    height: 1200,
    size_bytes: 250_000,
    uploader_name: uploader,
    caption,
    created_at: new Date(Date.now() - 60_000).toISOString(),
  };
}

const PHOTO_ANA = makePhoto("a".repeat(32), "Ana", "First dance!");
const PHOTO_ANON = makePhoto("b".repeat(32), null);

let feed: LiveFeed;

function makeFeed(overrides: Partial<LiveFeed> = {}): LiveFeed {
  return {
    retry: vi.fn(),
    applyEvent: vi.fn(),
    event: EVENT,
    photos: [PHOTO_ANA, PHOTO_ANON],
    status: "open",
    gone: null,
    error: null,
    initialLoading: false,
    hasMore: false,
    loadingMore: false,
    loadMore: vi.fn(async () => undefined),
    removePhoto: vi.fn(),
    latestLiveId: null,
    ...overrides,
  };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}${location.hash}`}</output>;
}

/**
 * Stands in for useLiveFeed: serves ``feed`` (read on every render, so a test
 * can swap it and call ``rerenderPage``) with the events the page applied.
 */
function useFakeLiveFeed(): LiveFeed {
  const [applied, setApplied] = useState<EventPublic | null>(null);
  return {
    ...feed,
    event: applied ?? feed.event,
    applyEvent: (event) => {
      feed.applyEvent(event);
      setApplied(event);
    },
  };
}

function renderPage(entry: string | { pathname: string; hash?: string; state?: unknown } = HOST_PATH) {
  const page = () => (
    <MemoryRouter initialEntries={[entry]}>
      <ToastProvider>
        <HostPage eventId={EVENT_ID} />
        <LocationProbe />
      </ToastProvider>
    </MemoryRouter>
  );
  const result = render(page());
  return { ...result, rerenderPage: () => result.rerender(page()) };
}

/** A returning host on this device: token and "Your events" record already stored. */
function signIn() {
  hostTokens.set(EVENT_ID, TOKEN);
  hostedEvents.add({ id: EVENT_ID, name: EVENT.name, createdAt: EVENT.created_at });
}

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();

  // jsdom has no modal dialogs or canvas rendering.
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => null);

  vi.mocked(api.getConfig).mockResolvedValue(CONFIG);
  vi.mocked(api.verifyHost).mockResolvedValue(undefined);
  vi.mocked(api.updateEvent).mockImplementation(async (_eventId, _token, changes) => ({ ...EVENT, ...changes }));
  vi.mocked(api.deletePhoto).mockResolvedValue(undefined);
  vi.mocked(api.deleteEvent).mockResolvedValue(undefined);

  feed = makeFeed();
  vi.mocked(useLiveFeed).mockImplementation(useFakeLiveFeed);
});

describe("HostPage access", () => {
  it("stores the token from a #token= link and removes it from the address", async () => {
    renderPage(`${HOST_PATH}#token=${TOKEN}`);

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe(HOST_PATH));
    expect(hostTokens.get(EVENT_ID)).toBe(TOKEN);
    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
    expect(api.verifyHost).toHaveBeenCalledWith(EVENT_ID, TOKEN);
    expect(useLiveFeed).toHaveBeenCalledWith(EVENT_ID, { hostToken: TOKEN });
    // First visit on this device: the event is remembered for the home page.
    expect(hostedEvents.list()).toEqual([{ id: EVENT_ID, name: EVENT.name, createdAt: EVENT.created_at }]);
  });

  it("strips a malformed token and asks for host access", async () => {
    renderPage(`${HOST_PATH}#token=not-a-token`);

    expect(screen.getByRole("heading", { level: 1, name: "Host access needed" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe(HOST_PATH));
    expect(hostTokens.get(EVENT_ID)).toBeNull();
  });

  it("asks for host access when this device has no token", () => {
    renderPage();

    expect(screen.getByRole("heading", { level: 1, name: "Host access needed" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View the wall" })).toHaveAttribute("href", `/e/${EVENT_ID}/wall`);
    expect(screen.getByRole("link", { name: "Upload photos" })).toHaveAttribute("href", `/e/${EVENT_ID}`);
    expect(api.verifyHost).not.toHaveBeenCalled();
    expect(useLiveFeed).not.toHaveBeenCalled();
  });

  it("shows the invalid-link state and forgets the token when verification is rejected", async () => {
    signIn();
    vi.mocked(api.verifyHost).mockRejectedValue(
      new ApiError({ status: 401, code: "UNAUTHORIZED", message: "The host token is not valid for this event." }),
    );
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "This host link is no longer valid" })).toBeInTheDocument();
    expect(hostTokens.get(EVENT_ID)).toBeNull();
  });

  it("shows event not found and forgets the event when it no longer exists", async () => {
    signIn();
    vi.mocked(api.verifyHost).mockRejectedValue(new ApiError({ status: 404, code: "EVENT_NOT_FOUND", message: "Not found" }));
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Event not found" })).toBeInTheDocument();
    expect(hostedEvents.list()).toEqual([]);
    expect(hostTokens.get(EVENT_ID)).toBeNull();
  });

  it("keeps the saved token until the server accepts a different host link", async () => {
    signIn();
    const newToken = "N".repeat(40);
    let accept = () => undefined as void;
    vi.mocked(api.verifyHost).mockImplementation((_eventId, token) =>
      token === newToken ? new Promise<void>((resolve) => (accept = () => resolve())) : Promise.resolve(),
    );
    renderPage(`${HOST_PATH}#token=${newToken}`);

    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe(HOST_PATH));
    expect(api.verifyHost).toHaveBeenCalledWith(EVENT_ID, newToken);
    expect(hostTokens.get(EVENT_ID)).toBe(TOKEN);

    accept();
    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
    expect(hostTokens.get(EVENT_ID)).toBe(newToken);
  });

  it("falls back to the saved token when a host link is rejected", async () => {
    signIn();
    const truncated = TOKEN.slice(0, -4);
    vi.mocked(api.verifyHost).mockImplementation(async (_eventId, token) => {
      if (token !== TOKEN) throw new ApiError({ status: 401, code: "UNAUTHORIZED", message: "Nope" });
    });
    renderPage(`${HOST_PATH}#token=${truncated}`);

    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
    expect(screen.getByText("That host link isn't valid")).toBeInTheDocument();
    expect(vi.mocked(api.verifyHost).mock.calls).toEqual([
      [EVENT_ID, truncated],
      [EVENT_ID, TOKEN],
    ]);
    expect(hostTokens.get(EVENT_ID)).toBe(TOKEN);
    expect(useLiveFeed).toHaveBeenLastCalledWith(EVENT_ID, { hostToken: TOKEN });
    expect(screen.getByTestId("location").textContent).toBe(HOST_PATH);
  });

  it("forgets a rejected host link when this device has no other token", async () => {
    vi.mocked(api.verifyHost).mockRejectedValue(new ApiError({ status: 401, code: "UNAUTHORIZED", message: "Nope" }));
    renderPage(`${HOST_PATH}#token=${TOKEN}`);

    expect(await screen.findByRole("heading", { level: 1, name: "This host link is no longer valid" })).toBeInTheDocument();
    expect(hostTokens.get(EVENT_ID)).toBeNull();
  });

  it("offers a retry when verification fails because of the network", async () => {
    signIn();
    vi.mocked(api.verifyHost).mockRejectedValueOnce(networkError());
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
    expect(api.verifyHost).toHaveBeenCalledTimes(2);
    expect(hostTokens.get(EVENT_ID)).toBe(TOKEN);
  });

  it("shows a deleted state when the live feed reports the event was deleted", async () => {
    signIn();
    feed = makeFeed({ gone: "deleted" });
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "This event was deleted" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to home" })).toHaveAttribute("href", "/");
  });
});

describe("HostPage dashboard", () => {
  it("offers the private host link, masked, after creating the event until dismissed", async () => {
    signIn();
    const hostUrl = `http://localhost:3000${HOST_PATH}#token=${TOKEN}`;
    const user = userEvent.setup();
    renderPage({ pathname: HOST_PATH, state: { justCreated: true } });

    const callout = (await screen.findByRole("heading", { name: "Save your private host link" })).closest("section")!;
    // Hidden by default: this screen is often shown to guests.
    expect(within(callout).getByText(`http://localhost:3000${HOST_PATH}#token=${TOKEN.slice(0, 4)}…`)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(TOKEN);
    expect(screen.queryByRole("textbox", { name: "Private host link" })).not.toBeInTheDocument();

    // Copying still gives the full link.
    await user.click(within(callout).getByRole("button", { name: "Copy host link" }));
    await expect(navigator.clipboard.readText()).resolves.toBe(hostUrl);

    const toggle = within(callout).getByRole("button", { name: "Show" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).toHaveAccessibleName("Hide");
    const field = within(callout).getByRole<HTMLInputElement>("textbox", { name: "Private host link" });
    expect(field).toHaveValue(hostUrl);
    expect(field).toHaveAttribute("readonly");
    await user.tab({ shift: true });
    expect(field).toHaveFocus();
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, hostUrl.length]);

    await user.click(toggle);
    expect(screen.queryByRole("textbox", { name: "Private host link" })).not.toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(TOKEN);

    await user.click(within(callout).getByRole("button", { name: "I've saved it" }));
    expect(screen.queryByRole("heading", { name: "Save your private host link" })).not.toBeInTheDocument();
    // Focus continues from the top of the page instead of getting lost.
    expect(screen.getByRole("heading", { level: 1, name: EVENT.name })).toHaveFocus();
  });

  it("shares the server's public links when the dashboard is opened on localhost", async () => {
    signIn();
    const origin = "https://photos.example.com";
    feed = makeFeed({ event: { ...EVENT, links: { upload: `${origin}/e/${EVENT_ID}`, wall: `${origin}/e/${EVENT_ID}/wall` } } });
    const user = userEvent.setup();
    renderPage({ pathname: HOST_PATH, state: { justCreated: true } });

    expect(await screen.findByRole("img", { name: `QR code linking to ${origin}/e/${EVENT_ID}` })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Upload link" })).toHaveValue(`${origin}/e/${EVENT_ID}`);
    await user.click(screen.getByRole("button", { name: "Show" }));
    expect(screen.getByRole("textbox", { name: "Private host link" })).toHaveValue(`${origin}${HOST_PATH}#token=${TOKEN}`);
  });

  it("retries loading the event through the live feed", async () => {
    signIn();
    feed = makeFeed({ event: null, photos: [], error: networkError() });
    const user = userEvent.setup();
    const { rerenderPage } = renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Couldn't load your event" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(feed.retry).toHaveBeenCalledTimes(1);

    // While the retry runs the error is cleared: show progress, not the old error.
    feed = { ...feed, error: null };
    rerenderPage();
    expect(screen.getByText("Opening your dashboard…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("does not show the host link reminder to a returning host", async () => {
    signIn();
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Save your private host link" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open wall/i })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("img", { name: `QR code linking to http://localhost:3000/e/${EVENT_ID}` })).toBeInTheDocument();
  });

  it("closes uploads optimistically", async () => {
    signIn();
    let finish: (value: EventPublic) => void = () => undefined;
    vi.mocked(api.updateEvent).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const user = userEvent.setup();
    renderPage();

    const toggle = await screen.findByRole("switch", { name: "Accept uploads" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);

    expect(api.updateEvent).toHaveBeenCalledWith(EVENT_ID, TOKEN, { uploads_open: false });
    // Before the server answers.
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("Uploads closed", { selector: "li" })).toBeInTheDocument();

    finish({ ...EVENT, uploads_open: false });
    expect(await screen.findByText("Guests can still watch the wall but can't add new photos.")).toBeInTheDocument();
    // The saved event is applied right away, without waiting for the live connection.
    expect(feed.applyEvent).toHaveBeenCalledWith({ ...EVENT, uploads_open: false });
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });

  it("rolls the uploads switch back when saving fails", async () => {
    signIn();
    vi.mocked(api.updateEvent).mockRejectedValue(networkError());
    const user = userEvent.setup();
    renderPage();

    const toggle = await screen.findByRole("switch", { name: "Accept uploads" });
    await user.click(toggle);

    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    expect(await screen.findByRole("alert")).toHaveTextContent("You're offline");
    expect(feed.applyEvent).not.toHaveBeenCalled();
  });

  it("switches to the invalid-link state when a host action is unauthorized", async () => {
    signIn();
    vi.mocked(api.updateEvent).mockRejectedValue(new ApiError({ status: 401, code: "UNAUTHORIZED", message: "Nope" }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("switch", { name: "Accept uploads" }));

    expect(await screen.findByRole("heading", { level: 1, name: "This host link is no longer valid" })).toBeInTheDocument();
    expect(hostTokens.get(EVENT_ID)).toBeNull();
  });

  it("renames the event", async () => {
    signIn();
    const user = userEvent.setup();
    renderPage();

    const field = await screen.findByRole("textbox", { name: "Event name" });
    await user.clear(field);
    await user.type(field, "  Garden Party ");
    await user.click(screen.getByRole("button", { name: "Save name" }));

    expect(api.updateEvent).toHaveBeenCalledWith(EVENT_ID, TOKEN, { name: "Garden Party" });
    expect(await screen.findByRole("heading", { level: 1, name: "Garden Party" })).toBeInTheDocument();
    expect(feed.applyEvent).toHaveBeenCalledWith({ ...EVENT, name: "Garden Party" });
    expect(hostedEvents.list()[0]?.name).toBe("Garden Party");
  });

  describe("deleting a photo, with undo", () => {
    const ANA_DELETE = "Delete photo by Ana: First dance!";

    afterEach(() => {
      vi.useRealTimers();
    });

    /** Shows the dashboard, then fakes the clock so tests can skip the undo time (plain clicks from here on). */
    async function renderWithClock() {
      signIn();
      const view = renderPage();
      await screen.findByRole("button", { name: ANA_DELETE });
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      return { view };
    }

    const photosRegion = () => screen.getByRole("region", { name: "Photos" });
    const skipUndoTime = () => act(async () => vi.advanceTimersByTime(UNDO_DELETE_MS));

    it("hides the photo at once and deletes it when the undo time is up", async () => {
      await renderWithClock();
      fireEvent.click(screen.getByRole("button", { name: ANA_DELETE }));

      // No confirmation dialog: the photo goes, with an Undo button for a few seconds.
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: ANA_DELETE })).not.toBeInTheDocument();
      expect(within(photosRegion()).getByText("1 photo")).toBeInTheDocument();
      expect(screen.getByText("Photo deleted")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Photos" })).toHaveFocus();
      expect(api.deletePhoto).not.toHaveBeenCalled();

      await skipUndoTime();
      expect(api.deletePhoto).toHaveBeenCalledWith(EVENT_ID, PHOTO_ANA.id, TOKEN);
      expect(feed.removePhoto).toHaveBeenCalledWith(PHOTO_ANA.id);
      expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    });

    it("brings the photo back and deletes nothing when the host taps Undo", async () => {
      await renderWithClock();
      fireEvent.click(screen.getByRole("button", { name: "Delete photo by a guest, 1 minute ago" }));
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));

      expect(screen.getByRole("button", { name: "Delete photo by a guest, 1 minute ago" })).toBeInTheDocument();
      expect(within(photosRegion()).getByText("2 photos")).toBeInTheDocument();
      expect(screen.getByText("Photo kept")).toBeInTheDocument();
      expect(screen.queryByText("Photo deleted")).not.toBeInTheDocument();

      await skipUndoTime();
      expect(api.deletePhoto).not.toHaveBeenCalled();
      expect(feed.removePhoto).not.toHaveBeenCalled();
    });

    it("can undo each of several deletes on its own", async () => {
      await renderWithClock();
      fireEvent.click(screen.getByRole("button", { name: ANA_DELETE }));
      fireEvent.click(screen.getByRole("button", { name: "Delete photo by a guest, 1 minute ago" }));
      expect(within(photosRegion()).getByText("0 photos")).toBeInTheDocument();

      // The newest toast's Undo brings back the second photo only.
      const undoButtons = screen.getAllByRole("button", { name: "Undo" });
      expect(undoButtons).toHaveLength(2);
      fireEvent.click(undoButtons[1]!);

      await skipUndoTime();
      expect(api.deletePhoto).toHaveBeenCalledTimes(1);
      expect(api.deletePhoto).toHaveBeenCalledWith(EVENT_ID, PHOTO_ANA.id, TOKEN);
    });

    it("shows the photo again if the delete fails", async () => {
      vi.mocked(api.deletePhoto).mockRejectedValue(networkError());
      await renderWithClock();
      fireEvent.click(screen.getByRole("button", { name: ANA_DELETE }));

      await skipUndoTime();
      expect(screen.getByRole("button", { name: ANA_DELETE })).toBeInTheDocument();
      expect(feed.removePhoto).not.toHaveBeenCalled();
    });

    it("sends a waiting delete at once when the host leaves the page", async () => {
      const { view } = await renderWithClock();
      fireEvent.click(screen.getByRole("button", { name: ANA_DELETE }));

      view.unmount();
      expect(api.deletePhoto).toHaveBeenCalledWith(EVENT_ID, PHOTO_ANA.id, TOKEN, { keepalive: true });
    });

    it("sends a waiting delete at once when the tab goes to the background", async () => {
      await renderWithClock();
      fireEvent.click(screen.getByRole("button", { name: ANA_DELETE }));

      const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      try {
        document.dispatchEvent(new Event("visibilitychange"));
      } finally {
        visibility.mockRestore();
      }
      expect(api.deletePhoto).toHaveBeenCalledWith(EVENT_ID, PHOTO_ANA.id, TOKEN, { keepalive: true });
      // Already sent: Undo can't bring it back any more, and the timer sends nothing twice.
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
      expect(screen.queryByRole("button", { name: ANA_DELETE })).not.toBeInTheDocument();
      await skipUndoTime();
      expect(api.deletePhoto).toHaveBeenCalledTimes(1);
    });
  });

  it("tells apart the delete buttons of photos by the same guest", async () => {
    signIn();
    const earlier = { ...makePhoto("c".repeat(32), "Ana"), created_at: new Date(Date.now() - 3 * 3600_000).toISOString() };
    feed = makeFeed({ photos: [PHOTO_ANA, makePhoto("d".repeat(32), "Ana"), earlier] });
    renderPage();

    expect(await screen.findByRole("button", { name: "Delete photo by Ana: First dance!" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete photo by Ana, 1 minute ago" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete photo by Ana, 3 hours ago" })).toBeInTheDocument();
  });

  it("shows an empty state and loads more photos on request", async () => {
    signIn();
    feed = makeFeed({ photos: [], event: { ...EVENT, photo_count: 0 } });
    const { unmount } = renderPage();
    expect(await screen.findByText("No photos yet")).toBeInTheDocument();
    unmount();

    feed = makeFeed({ hasMore: true });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Load more photos" }));
    expect(feed.loadMore).toHaveBeenCalled();
  });

  it("requires typing “delete” before deleting the event", async () => {
    signIn();
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete event" }));
    const dialog = screen.getByRole("dialog", { name: "Delete this event?" });
    const confirm = within(dialog).getByRole("button", { name: "Delete forever" });
    const input = within(dialog).getByRole("textbox", { name: "Type “delete” to confirm" });
    expect(confirm).toBeDisabled();

    await user.type(input, "delet");
    expect(confirm).toBeDisabled();
    await user.keyboard("{Enter}");
    expect(api.deleteEvent).not.toHaveBeenCalled();

    await user.type(input, "e");
    expect(confirm).toBeEnabled();
    await user.click(confirm);

    await waitFor(() => expect(api.deleteEvent).toHaveBeenCalledWith(EVENT_ID, TOKEN));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/"));
    expect(hostedEvents.list()).toEqual([]);
    expect(hostTokens.get(EVENT_ID)).toBeNull();
    expect(await screen.findByText("Event deleted")).toBeInTheDocument();
  });

  it("goes home without flashing the deleted screen when the live feed announces the deletion first", async () => {
    signIn();
    let finish = () => undefined as void;
    vi.mocked(api.deleteEvent).mockReturnValue(new Promise<void>((resolve) => (finish = () => resolve())));
    const user = userEvent.setup();
    const { rerenderPage } = renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete event" }));
    const dialog = screen.getByRole("dialog", { name: "Delete this event?" });
    await user.type(within(dialog).getByRole("textbox", { name: "Type “delete” to confirm" }), "delete");
    await user.click(within(dialog).getByRole("button", { name: "Delete forever" }));

    // The live "event deleted" message beats the API response.
    feed = { ...feed, gone: "deleted" };
    rerenderPage();
    expect(screen.queryByRole("heading", { name: "This event was deleted" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();

    finish();
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/"));
    expect(screen.queryByRole("heading", { name: "This event was deleted" })).not.toBeInTheDocument();
    expect(await screen.findByText("Event deleted")).toBeInTheDocument();
  });
});
