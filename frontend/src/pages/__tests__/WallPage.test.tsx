import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../../components/ui/Toast";
import { SLIDE_DURATION_MS } from "../../components/wall/Slideshow";
import { type LiveFeed, useLiveFeed } from "../../hooks/useLiveFeed";
import { ApiError } from "../../lib/api";
import { mediaUrl } from "../../lib/env";
import type { EventPublic, Photo } from "../../lib/types";
import WallPage from "../WallPage";

vi.mock("../../hooks/useLiveFeed", () => ({ useLiveFeed: vi.fn() }));
// The real QR code draws on a <canvas>, which jsdom doesn't implement.
vi.mock("../../components/EventQrCode", () => ({
  EventQrCode: ({ url }: { url: string }) => <div data-testid="event-qr" data-url={url} />,
}));

const EVENT_ID = "k3x9p2m7q1ab";

const event: EventPublic = {
  id: EVENT_ID,
  name: "Aisyah & Daniel's Wedding",
  uploads_open: true,
  photo_count: 3,
  created_at: "2026-09-28T10:00:00Z",
  links: { upload: `http://localhost/e/${EVENT_ID}`, wall: `http://localhost/e/${EVENT_ID}/wall` },
};

function makePhoto(n: number, overrides: Partial<Photo> = {}): Photo {
  const hex = n.toString(16).padStart(32, "0");
  return {
    id: hex,
    event_id: EVENT_ID,
    url: `/media/events/${EVENT_ID}/photos/${hex}.jpg`,
    thumbnail_url: `/media/events/${EVENT_ID}/thumbs/${hex}.webp`,
    content_type: "image/jpeg",
    width: 1600,
    height: 1200,
    size_bytes: 250_000,
    uploader_name: null,
    caption: null,
    created_at: new Date(Date.now() - n * 60_000).toISOString(),
    ...overrides,
  };
}

const photos = [
  makePhoto(1, { uploader_name: "Aisyah", caption: "Cutting the cake" }),
  makePhoto(2, { uploader_name: "Daniel" }),
  makePhoto(3),
];

function feed(overrides: Partial<LiveFeed> = {}): LiveFeed {
  return {
    retry: vi.fn(),
    applyEvent: vi.fn(),
    event,
    photos,
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

function renderWall(value: LiveFeed, path = `/e/${EVENT_ID}/wall`) {
  vi.mocked(useLiveFeed).mockReturnValue(value);
  const ui = () => (
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <WallPage eventId={EVENT_ID} />
      </ToastProvider>
    </MemoryRouter>
  );
  const result = render(ui());
  return {
    ...result,
    /** Re-render with a new feed value (e.g. a live photo arriving). */
    update: (next: LiveFeed) => {
      vi.mocked(useLiveFeed).mockReturnValue(next);
      result.rerender(ui());
    },
  };
}

const observers: Array<{ callback: IntersectionObserverCallback; target: Element | null }> = [];

function stubMatchMedia(matching: (query: string) => boolean = () => false) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matching(query),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

/** jsdom has no Web Animations API: stand in for Element.animate (removed again in afterEach). */
function stubAnimate() {
  const animate = vi.fn(() => ({ cancel: vi.fn(), pause: vi.fn(), play: vi.fn() }));
  Object.defineProperty(HTMLElement.prototype, "animate", { value: animate, configurable: true, writable: true });
  return animate;
}

const offline = () => new ApiError({ status: 0, code: "NETWORK_ERROR", message: "Can't reach the server." });

beforeEach(() => {
  observers.length = 0;
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement) {
    this.removeAttribute("open");
  };
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      private readonly entry: { callback: IntersectionObserverCallback; target: Element | null };
      constructor(callback: IntersectionObserverCallback) {
        this.entry = { callback, target: null };
        observers.push(this.entry);
      }
      observe(target: Element) {
        this.entry.target = target;
      }
      unobserve() {}
      disconnect() {
        this.entry.target = null;
      }
      takeRecords() {
        return [];
      }
    },
  );
  stubMatchMedia();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, "animate");
});

describe("WallPage", () => {
  it("renders the photos as thumbnails from mediaUrl", () => {
    renderWall(feed());

    expect(screen.getByRole("heading", { level: 1, name: event.name })).toBeInTheDocument();
    expect(screen.getByText("3 photos")).toBeInTheDocument();
    const tiles = screen.getAllByRole("button", { name: /photo|cake/i }).filter((button) => button.dataset.photoId);
    expect(tiles).toHaveLength(3);

    const cake = screen.getByRole("img", { name: "Cutting the cake (photo by Aisyah)" });
    expect(cake).toHaveAttribute("src", mediaUrl(photos[0]!.thumbnail_url));
    expect(cake).toHaveAttribute("loading", "lazy");
    expect(screen.getByRole("img", { name: "Photo by Daniel" })).toHaveAttribute("src", mediaUrl(photos[1]!.thumbnail_url));
    expect(screen.getByRole("img", { name: "Guest photo" })).toHaveAttribute("src", mediaUrl(photos[2]!.thumbnail_url));
    expect(screen.getByRole("link", { name: "Add photos" })).toHaveAttribute("href", `/e/${EVENT_ID}`);
    // Laptops and desktops can add photos too, not just phones.
    expect(screen.getByRole("link", { name: "Add photos" }).className).not.toMatch(/\b(lg|xl|2xl):hidden\b/);
  });

  it("lays the photos out in rows of square tiles, newest first, readable from across the room", () => {
    const { update } = renderWall(feed());

    const list = screen.getByRole("list");
    expect(list).toHaveClass("grid");
    // jsdom's 1024 px window: the automatic large-screen layout, 4 per row.
    expect(list.style.gridTemplateColumns).toBe("repeat(4, minmax(0, 1fr))");
    expect(list.className).not.toMatch(/columns-/);
    const alts = () => within(list).getAllByRole("img").map((image) => image.getAttribute("alt"));
    expect(alts()).toEqual(["Cutting the cake (photo by Aisyah)", "Photo by Daniel", "Guest photo"]);

    const cake = screen.getByRole("img", { name: "Cutting the cake (photo by Aisyah)" });
    expect(cake).toHaveClass("block", "aspect-square", "w-full", "object-cover");
    expect(cake.style.aspectRatio).toBe("");
    expect(cake).toHaveAttribute("width", "1600");
    expect(cake).toHaveAttribute("height", "1200");
    expect(cake.closest("li")).not.toHaveClass("break-inside-avoid");

    // Bigger text on a TV (2xl) for the tile overlay and the photo count.
    expect(screen.getByText("Cutting the cake")).toHaveClass("text-sm", "2xl:text-lg");
    expect(screen.getByText("Aisyah")).toHaveClass("text-xs", "2xl:text-base");
    expect(screen.getByText("Cutting the cake").parentElement).toHaveClass("2xl:px-4", "2xl:pb-4");
    expect(screen.getByText("3 photos")).toHaveClass("text-sm", "2xl:text-lg");

    // Clear of the notch on an iPhone in landscape.
    expect(screen.getByRole("region", { name: "Photos" })).toHaveClass(
      "pl-[max(0.75rem,env(safe-area-inset-left))]",
      "pr-[max(0.75rem,env(safe-area-inset-right))]",
      "sm:pl-[max(1rem,env(safe-area-inset-left))]",
      "sm:pr-[max(1rem,env(safe-area-inset-right))]",
    );

    // A live arrival takes the first cell and pops in; everything else keeps its order.
    const live = makePhoto(0, { uploader_name: "Mak Long" });
    update(feed({ photos: [live, ...photos], latestLiveId: live.id, event: { ...event, photo_count: 4 } }));
    expect(alts()).toEqual(["Photo by Mak Long", "Cutting the cake (photo by Aisyah)", "Photo by Daniel", "Guest photo"]);
    expect(within(list).getAllByRole("listitem")[0]).toHaveClass("animate-pop-in");
    expect(within(list).getAllByRole("listitem")[1]).not.toHaveClass("animate-pop-in");
  });

  it("subscribes with the host token when this device hosts the event", () => {
    const token = "h".repeat(40);
    window.localStorage.setItem(`gk.host.${EVENT_ID}`, token);
    renderWall(feed());
    expect(useLiveFeed).toHaveBeenCalledWith(EVENT_ID, { hostToken: token });
  });

  it("loads older photos when the end of the grid scrolls into view", () => {
    const loadMore = vi.fn(async () => undefined);
    renderWall(feed({ hasMore: true, loadMore }));

    const observer = observers.find((entry) => entry.target);
    expect(observer).toBeDefined();
    act(() => observer!.callback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(loadMore).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Load older photos" })).toBeInTheDocument();
  });

  it("shows a big QR code while there are no photos yet", () => {
    renderWall(feed({ photos: [], event: { ...event, photo_count: 0 } }));

    expect(screen.getByRole("heading", { name: "Scan to share the first photo" })).toBeInTheDocument();
    expect(screen.getByTestId("event-qr")).toHaveAttribute("data-url", `${window.location.origin}/e/${EVENT_ID}`);
    expect(screen.getByText("Waiting for photos…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "QR code for guests" })).not.toBeInTheDocument();
  });

  it("points the QR codes at the server's public link when this screen runs on localhost", async () => {
    const user = userEvent.setup();
    const publicLink = `https://gambarkita.example/e/${EVENT_ID}`;
    const shared = { ...event, links: { upload: publicLink, wall: `${publicLink}/wall` } };
    expect(window.location.hostname).toBe("localhost");

    const { unmount } = renderWall(feed({ photos: [], event: { ...shared, photo_count: 0 } }));
    expect(screen.getByTestId("event-qr")).toHaveAttribute("data-url", publicLink);
    expect(screen.getByText(`gambarkita.example/e/${EVENT_ID}`)).toBeInTheDocument();
    unmount();

    renderWall(feed({ event: shared }));
    await user.click(screen.getByRole("button", { name: "QR code for guests" }));
    const panel = screen.getByRole("complementary", { name: "Join this wall" });
    expect(within(panel).getByTestId("event-qr")).toHaveAttribute("data-url", publicLink);
    expect(within(panel).getByText(`gambarkita.example/e/${EVENT_ID}`)).toHaveClass("text-xs", "2xl:text-sm");
    // The on-screen "Add photos" button stays an in-app link.
    expect(screen.getByRole("link", { name: "Add photos" })).toHaveAttribute("href", `/e/${EVENT_ID}`);
  });

  it("shows a toast when older photos fail to load from the grid", async () => {
    const user = userEvent.setup();
    const loadMore = vi.fn(async () => {
      throw offline();
    });
    renderWall(feed({ hasMore: true, loadMore }));

    await user.click(screen.getByRole("button", { name: "Load older photos" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("You're offline");
    expect(loadMore).toHaveBeenCalledTimes(1);
  });

  it("shows a failed load of older photos inside the lightbox, with a retry", async () => {
    const user = userEvent.setup();
    const loadMore = vi.fn<() => Promise<void>>().mockRejectedValueOnce(offline()).mockResolvedValue(undefined);
    renderWall(feed({ hasMore: true, loadMore }));

    // The oldest loaded photo: nothing older to step to, so the viewer asks for the next page.
    await user.click(screen.getByRole("button", { name: "Guest photo" }));
    const dialog = screen.getByRole("dialog", { name: "Photo 3 of 3" });
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load older photos.");
    expect(loadMore).toHaveBeenCalledTimes(1);
    // No toast: it would be hidden underneath the modal dialog.
    expect(screen.getAllByRole("alert")).toEqual([alert]);
    expect(screen.queryByText("You're offline")).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument());
    expect(loadMore.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps the error out of the lightbox while there is still an older photo to show", async () => {
    const user = userEvent.setup();
    const loadMore = vi.fn(async () => {
      throw offline();
    });
    renderWall(feed({ hasMore: true, loadMore }));

    await user.click(screen.getByRole("button", { name: "Cutting the cake (photo by Aisyah)" }));
    const dialog = screen.getByRole("dialog", { name: "Photo 1 of 3" });
    await waitFor(() => expect(loadMore).toHaveBeenCalledTimes(1));
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("You're offline")).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Older photo" }));
    await user.click(within(dialog).getByRole("button", { name: "Older photo" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Couldn't load older photos.");
  });

  it("says the event has ended when the host deleted it", () => {
    renderWall(feed({ gone: "deleted", event: null, photos: [] }));
    expect(screen.getByRole("heading", { level: 1, name: "This event has ended" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Go to home" })).not.toBeInTheDocument();
  });

  it("says the event was not found", () => {
    renderWall(feed({ gone: "not_found", event: null, photos: [] }));
    expect(screen.getByRole("heading", { level: 1, name: "Event not found" })).toBeInTheDocument();
    // Always dark, whatever the system theme.
    expect(screen.getByText("Check the link or scan the event's QR code again.")).toHaveClass("text-stone-300");
  });

  it("offers a retry when the event couldn't be loaded", () => {
    renderWall(feed({ event: null, photos: [], error: offline() }));
    expect(screen.getByRole("heading", { level: 1, name: "You're offline" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("keeps showing the photos when a refresh fails after they loaded", () => {
    renderWall(feed({ error: new Error("boom"), status: "reconnecting" }));
    expect(screen.getByRole("img", { name: "Photo by Daniel" })).toBeInTheDocument();
    expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
  });

  it("switches to a slideshow that shows one photo and advances with ArrowRight", async () => {
    const user = userEvent.setup();
    renderWall(feed());

    await user.click(screen.getByRole("button", { name: "Switch to slideshow" }));

    const first = screen.getByRole("group", { name: "Photo 1 of 3" });
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(within(first).getByRole("img", { name: "Cutting the cake (photo by Aisyah)" })).toHaveAttribute("src", mediaUrl(photos[0]!.url));
    expect(screen.queryByRole("list")).not.toBeInTheDocument();

    await user.keyboard("{ArrowRight}");
    const second = screen.getByRole("group", { name: "Photo 2 of 3" });
    expect(screen.getAllByRole("group")).toHaveLength(1);
    expect(within(second).getByRole("img", { name: "Photo by Daniel" })).toHaveAttribute("src", mediaUrl(photos[1]!.url));

    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("group", { name: "Photo 1 of 3" })).toBeInTheDocument();
  });

  it("advances the slideshow on its own, pauses with Space and puts new live photos first", () => {
    // fireEvent rather than user-event: RTL's async wrapper waits on a real
    // setTimeout that Vitest's fake timers never fire.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const { update } = renderWall(feed(), `/e/${EVENT_ID}/wall?view=slideshow`);
    const pressSpace = () => fireEvent.keyDown(document.activeElement ?? document.body, { key: " " });

    expect(screen.getByRole("group", { name: "Photo 1 of 3" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(SLIDE_DURATION_MS));
    expect(screen.getByRole("group", { name: "Photo 2 of 3" })).toBeInTheDocument();

    pressSpace();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume slideshow" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(SLIDE_DURATION_MS * 2));
    expect(screen.getByRole("group", { name: "Photo 2 of 3" })).toBeInTheDocument();
    pressSpace();
    expect(screen.queryByText("Paused")).not.toBeInTheDocument();

    const live = makePhoto(0, { uploader_name: "Mak Long" });
    update(feed({ photos: [live, ...photos], latestLiveId: live.id, event: { ...event, photo_count: 4 } }));
    act(() => vi.advanceTimersByTime(SLIDE_DURATION_MS));

    const fresh = screen.getByRole("group", { name: "Photo 1 of 4" });
    expect(within(fresh).getByRole("img", { name: "Photo by Mak Long" })).toBeInTheDocument();
    expect(screen.getByText("New photo")).toBeInTheDocument();

    // Then the rotation carries on after the photo it was showing before.
    act(() => vi.advanceTimersByTime(SLIDE_DURATION_MS));
    expect(screen.getByRole("group", { name: "Photo 4 of 4" })).toBeInTheDocument();
    expect(screen.queryByText("New photo")).not.toBeInTheDocument();
  });

  it("animates the slideshow progress bar, unless reduced motion is preferred", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const animate = stubAnimate();
    const { unmount } = renderWall(feed(), `/e/${EVENT_ID}/wall?view=slideshow`);
    expect(animate).toHaveBeenCalledTimes(1);
    unmount();

    animate.mockClear();
    stubMatchMedia((query) => query === "(prefers-reduced-motion: reduce)");
    renderWall(feed(), `/e/${EVENT_ID}/wall?view=slideshow`);
    expect(screen.getByRole("group", { name: "Photo 1 of 3" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(SLIDE_DURATION_MS));
    // Slides still advance; only the bar stays still.
    expect(screen.getByRole("group", { name: "Photo 2 of 3" })).toBeInTheDocument();
    expect(animate).not.toHaveBeenCalled();
  });

  it("opens a lightbox on click and closes it with Escape", async () => {
    const user = userEvent.setup();
    renderWall(feed());
    const tile = screen.getByRole("button", { name: "Cutting the cake (photo by Aisyah)" });

    await user.click(tile);
    const dialog = screen.getByRole("dialog", { name: "Photo 1 of 3" });
    expect(within(dialog).getByRole("img", { name: "Cutting the cake (photo by Aisyah)" })).toHaveAttribute(
      "src",
      mediaUrl(photos[0]!.url),
    );
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("dialog", { name: "Photo 2 of 3" })).toBeInTheDocument();
    await user.keyboard("{ArrowLeft}");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(tile).toHaveFocus();
  });

  it("returns focus to the tile of the last photo viewed", async () => {
    const user = userEvent.setup();
    renderWall(feed());

    await user.click(screen.getByRole("button", { name: "Cutting the cake (photo by Aisyah)" }));
    await user.click(screen.getByRole("button", { name: "Older photo" }));
    await user.click(screen.getByRole("button", { name: "Close" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Photo by Daniel" })).toHaveFocus();
  });
});

describe("WallPage grid zoom", () => {
  const columns = () => screen.getByRole("list").style.gridTemplateColumns;
  const perRow = (count: number) => `repeat(${count}, minmax(0, 1fr))`;

  /** A touch event with these finger positions (jsdom can't build real Touch objects). */
  function touch(target: Element, type: "touchstart" | "touchmove" | "touchend", fingers: Array<[number, number]>) {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "touches", { value: fingers.map(([clientX, clientY]) => ({ clientX, clientY })) });
    act(() => {
      target.dispatchEvent(event);
    });
    return event;
  }

  it("changes the photos per row with the zoom buttons and remembers it on this device", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWall(feed());
    expect(columns()).toBe(perRow(4));

    await user.click(screen.getByRole("button", { name: "Bigger photos" }));
    expect(columns()).toBe(perRow(3));
    await user.click(screen.getByRole("button", { name: "Smaller photos" }));
    await user.click(screen.getByRole("button", { name: "Smaller photos" }));
    expect(columns()).toBe(perRow(5));
    expect(window.localStorage.getItem("gk.wall-columns")).toBe("5");

    unmount();
    renderWall(feed());
    expect(columns()).toBe(perRow(5));
  });

  it("stops at one photo per row, which shows the whole photo uncropped", async () => {
    window.localStorage.setItem("gk.wall-columns", "2");
    const user = userEvent.setup();
    renderWall(feed());

    await user.click(screen.getByRole("button", { name: "Bigger photos" }));
    expect(columns()).toBe(perRow(1));
    expect(screen.getByRole("button", { name: "Bigger photos" })).toBeDisabled();
    const cake = screen.getByRole("img", { name: "Cutting the cake (photo by Aisyah)" });
    expect(cake).not.toHaveClass("aspect-square");
    expect(cake.style.aspectRatio).toBe("1600 / 1200");
    // Big tiles may load the full photo, so it stays sharp.
    expect(cake.getAttribute("srcset")).toContain(`${mediaUrl(`/media/events/${EVENT_ID}/photos/`)}`);
  });

  it("zooms with a two-finger pinch instead of zooming the page", () => {
    renderWall(feed());
    const grid = screen.getByRole("region", { name: "Photos" });

    // Fingers spread to twice the distance: bigger photos, one fewer per row.
    touch(grid, "touchstart", [[100, 300], [200, 300]]);
    expect(touch(grid, "touchmove", [[50, 300], [250, 300]]).defaultPrevented).toBe(true);
    touch(grid, "touchend", []);
    expect(columns()).toBe(perRow(3));

    // Fingers close in: smaller photos.
    touch(grid, "touchstart", [[50, 300], [250, 300]]);
    touch(grid, "touchmove", [[100, 300], [200, 300]]);
    touch(grid, "touchend", []);
    expect(columns()).toBe(perRow(4));

    // One finger just scrolls.
    touch(grid, "touchstart", [[100, 300]]);
    expect(touch(grid, "touchmove", [[100, 200]]).defaultPrevented).toBe(false);
    expect(columns()).toBe(perRow(4));
  });

  it("zooms with a trackpad pinch or Ctrl + mouse wheel, but leaves plain scrolling alone", () => {
    renderWall(feed());
    const grid = screen.getByRole("region", { name: "Photos" });

    expect(fireEvent.wheel(grid, { ctrlKey: true, deltaY: -100 })).toBe(false);
    expect(columns()).toBe(perRow(3));
    fireEvent.wheel(grid, { ctrlKey: true, deltaY: 100 });
    fireEvent.wheel(grid, { ctrlKey: true, deltaY: 100 });
    expect(columns()).toBe(perRow(5));

    expect(fireEvent.wheel(grid, { deltaY: 100 })).toBe(true);
    expect(columns()).toBe(perRow(5));
  });

  it("has no zoom buttons in the slideshow", () => {
    renderWall(feed(), `/e/${EVENT_ID}/wall?view=slideshow`);
    expect(screen.queryByRole("button", { name: "Bigger photos" })).not.toBeInTheDocument();
  });
});
