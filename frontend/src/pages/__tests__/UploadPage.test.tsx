import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from "vitest";

import { ToastProvider } from "../../components/ui/Toast";
import { resetUploadQueues } from "../../components/upload/useUploadQueue";
import { DEFAULT_CONFIG } from "../../hooks/usePublicConfig";
import { ApiError, api, networkError } from "../../lib/api";
import { preparePhoto } from "../../lib/image";
import { guestName } from "../../lib/storage";
import type { EventPublic, GuestQuota, Photo, PublicConfig } from "../../lib/types";
import { type UploadOptions, uploadPhoto } from "../../lib/upload";
import UploadPage from "../UploadPage";

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return { ...actual, api: { getEvent: vi.fn(), getConfig: vi.fn(), getGuestQuota: vi.fn() } };
});
vi.mock("../../lib/image", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/image")>()),
  preparePhoto: vi.fn(),
}));
vi.mock("../../lib/upload", () => ({ uploadPhoto: vi.fn() }));

// Whether /api/config has answered, and with what, is set per test.
const publicConfig = vi.hoisted(() => ({ loaded: true, overrides: {} as Partial<PublicConfig> }));
vi.mock("../../hooks/usePublicConfig", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../hooks/usePublicConfig")>();
  return {
    ...actual,
    usePublicConfig: () => ({
      config: { ...actual.DEFAULT_CONFIG, ...publicConfig.overrides },
      loaded: publicConfig.loaded,
    }),
  };
});

const EVENT_ID = "abcdef123456";

const EVENT: EventPublic = {
  id: EVENT_ID,
  name: "Aisyah & Daniel's Wedding",
  uploads_open: true,
  photo_count: 10,
  created_at: "2026-09-28T10:00:00Z",
  links: { upload: `/e/${EVENT_ID}`, wall: `/e/${EVENT_ID}/wall` },
};

function photo(id: string): Photo {
  return {
    id,
    event_id: EVENT_ID,
    url: `/media/events/${EVENT_ID}/photos/${"a".repeat(32)}.jpg`,
    thumbnail_url: `/media/events/${EVENT_ID}/thumbs/${"a".repeat(32)}.webp`,
    content_type: "image/jpeg",
    width: 800,
    height: 600,
    size_bytes: 1234,
    uploader_name: null,
    caption: null,
    created_at: "2026-09-28T12:00:00Z",
  };
}

function jpeg(name: string): File {
  return new File(["jpeg-bytes"], name, { type: "image/jpeg" });
}

interface PendingUpload {
  options: UploadOptions;
  resolve: (photo: Photo) => void;
  reject: (error: unknown) => void;
}

let pending: PendingUpload[];
let scrollIntoView: Mock<(options?: ScrollIntoViewOptions) => void>;

function upload(index: number): PendingUpload {
  const item = pending[index];
  if (!item) throw new Error(`No upload #${index}`);
  return item;
}

function renderPage() {
  return render(
    <StrictMode>
      <MemoryRouter>
        <ToastProvider>
          <UploadPage eventId={EVENT_ID} />
        </ToastProvider>
      </MemoryRouter>
    </StrictMode>,
  );
}

async function renderOpenEvent() {
  vi.mocked(api.getEvent).mockResolvedValue(EVENT);
  const user = userEvent.setup();
  const view = renderPage();
  await screen.findByRole("heading", { level: 1, name: EVENT.name });
  return { user, view };
}

const galleryInput = () => screen.getByLabelText<HTMLInputElement>("Choose photos from your gallery");
const cameraInput = () => screen.getByLabelText<HTMLInputElement>("Take a photo with your camera");

/** Picks photos and confirms sharing them on the wall, as a guest does. */
async function share(user: ReturnType<typeof userEvent.setup>, input: HTMLInputElement, files: File | File[]) {
  await user.upload(input, files);
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: /^Share \d+ photos?$/ }));
}

/** Whether closing or reloading the tab would ask for confirmation. */
function unloadIsGuarded(): boolean {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

beforeEach(() => {
  pending = [];
  publicConfig.loaded = true;
  publicConfig.overrides = {};
  // jsdom has no modal dialogs.
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute("open", "");
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute("open");
  });
  // jsdom doesn't lay out or scroll.
  scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;
  Object.assign(URL, {
    createObjectURL: vi.fn((file: File) => `blob:${file.name}`),
    revokeObjectURL: vi.fn(),
  });
  vi.mocked(api.getConfig).mockResolvedValue(DEFAULT_CONFIG);
  vi.mocked(preparePhoto).mockImplementation(async (file) => ({ blob: file, filename: file.name, converted: false }));
  vi.mocked(uploadPhoto).mockImplementation(
    (_eventId, _blob, _filename, options = {}) =>
      new Promise<Photo>((resolve, reject) => pending.push({ options, resolve, reject })),
  );
});

afterEach(() => {
  // Queues outlive the page on purpose; don't let one test's uploads leak into the next.
  resetUploadQueues();
  vi.unstubAllGlobals();
});

describe("UploadPage", () => {
  it("shows a not-found screen for an unknown event", async () => {
    vi.mocked(api.getEvent).mockRejectedValue(
      new ApiError({ status: 404, code: "EVENT_NOT_FOUND", message: "Event not found." }),
    );
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Event not found" })).toBeInTheDocument();
    // Guests get no way to the home page: neither a button nor a clickable logo.
    expect(screen.queryByRole("link", { name: "Go to home" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "GambarKita home" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Wall" })).not.toBeInTheDocument();
  });

  it("offers a retry when the event can't be loaded", async () => {
    // State-based rather than call-order-based: StrictMode fetches (and aborts) once extra on mount.
    let online = false;
    vi.mocked(api.getEvent).mockImplementation(async () => {
      if (!online) throw networkError();
      return EVENT;
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Couldn't load this event" })).toBeInTheDocument();
    expect(screen.getByText(/can't reach the server/i)).toBeInTheDocument();
    online = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
  });

  it("explains when the host has closed uploads, and picks up a re-opening", async () => {
    let uploadsOpen = false;
    vi.mocked(api.getEvent).mockImplementation(async () => ({ ...EVENT, uploads_open: uploadsOpen }));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "The host has closed uploads" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View the photo wall" })).toHaveAttribute("href", `/e/${EVENT_ID}/wall`);
    expect(screen.queryByRole("button", { name: "Take a photo" })).not.toBeInTheDocument();

    uploadsOpen = true;
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByRole("heading", { level: 1, name: EVENT.name })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Take a photo" })).toBeInTheDocument();
  });

  it("shows the event with both pickers, the photo count and a wall link", async () => {
    await renderOpenEvent();

    expect(screen.getByText(/share your photos/i)).toBeInTheDocument();
    expect(screen.getByText("10 photos on the wall")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Take a photo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Choose from gallery" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Wall" })).toHaveAttribute("href", `/e/${EVENT_ID}/wall`);
    expect(cameraInput()).toHaveAttribute("capture", "environment");
    expect(galleryInput()).toHaveAttribute("multiple");
    expect(screen.getByText("Your photo's location is removed before it's saved or shown.")).toBeInTheDocument();
  });

  describe("confirming before photos go on the wall", () => {
    const tile = (dialog: HTMLElement, number: number) => within(dialog).getByRole("button", { name: `Share photo ${number}` });
    const tileSrc = (dialog: HTMLElement, number: number) => tile(dialog, number).querySelector("img")?.getAttribute("src");

    it("shows the picked photos and uploads nothing until the guest confirms", async () => {
      const { user } = await renderOpenEvent();
      await user.upload(galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);

      const dialog = await screen.findByRole("dialog", { name: "Share these 2 photos on the live wall?" });
      expect(tileSrc(dialog, 1)).toBe("blob:one.jpg");
      expect(tileSrc(dialog, 2)).toBe("blob:two.jpg");
      expect(tile(dialog, 1)).toHaveAttribute("aria-pressed", "true");
      expect(tile(dialog, 2)).toHaveAttribute("aria-pressed", "true");
      // Without a per-guest limit there is no count to show.
      expect(within(dialog).queryByText(/chosen$/)).not.toBeInTheDocument();
      expect(preparePhoto).not.toHaveBeenCalled();
      expect(uploadPhoto).not.toHaveBeenCalled();
      expect(screen.queryByRole("list", { name: "Your photos" })).not.toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "Share 2 photos" }));
      await waitFor(() => expect(uploadPhoto).toHaveBeenCalledTimes(2));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      // The queue reuses the previews made for the confirmation.
      expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
      expect(URL.revokeObjectURL).not.toHaveBeenCalled();
      const grid = screen.getByRole("list", { name: "Your photos" });
      expect(within(grid).getByRole("img", { name: "Photo 1" })).toHaveAttribute("src", "blob:one.jpg");
    });

    it("asks about a single camera shot too, and can't share nothing", async () => {
      const { user } = await renderOpenEvent();
      await user.upload(cameraInput(), jpeg("party.jpg"));

      const dialog = await screen.findByRole("dialog", { name: "Share this photo on the live wall?" });
      expect(
        within(dialog).getByText("Everyone at the event will see it. Only the host can remove a photo once it's shared."),
      ).toBeInTheDocument();

      await user.click(tile(dialog, 1));
      expect(screen.getByRole("dialog", { name: "Choose photos to share" })).toBe(dialog);
      expect(within(dialog).getByRole("button", { name: "Share 0 photos" })).toBeDisabled();

      await user.click(tile(dialog, 1));
      await user.click(within(dialog).getByRole("button", { name: "Share 1 photo" }));
      await waitFor(() => expect(uploadPhoto).toHaveBeenCalledTimes(1));
    });

    it("uploads nothing and keeps the message when the guest cancels", async () => {
      const { user } = await renderOpenEvent();
      await user.type(screen.getByLabelText(/message/i), "Congrats!");
      await user.upload(galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);

      const dialog = await screen.findByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:one.jpg");
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:two.jpg");
      expect(uploadPhoto).not.toHaveBeenCalled();
      expect(screen.queryByRole("list", { name: "Your photos" })).not.toBeInTheDocument();
      expect(screen.getByLabelText(/message/i)).toHaveValue("Congrats!");
    });

    it("lets the guest leave out a photo before sharing", async () => {
      const { user } = await renderOpenEvent();
      const files = [jpeg("one.jpg"), jpeg("two.jpg"), jpeg("three.jpg")];
      await user.upload(galleryInput(), files);

      const dialog = await screen.findByRole("dialog", { name: "Share these 3 photos on the live wall?" });
      await user.click(tile(dialog, 2));
      expect(tile(dialog, 2)).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByRole("dialog", { name: "Share these 2 photos on the live wall?" })).toBe(dialog);

      await user.click(within(dialog).getByRole("button", { name: "Share 2 photos" }));
      await waitFor(() => expect(uploadPhoto).toHaveBeenCalledTimes(2));
      expect(uploadPhoto).toHaveBeenCalledWith(EVENT_ID, files[0], "one.jpg", expect.anything());
      expect(uploadPhoto).toHaveBeenCalledWith(EVENT_ID, files[2], "three.jpg", expect.anything());
      // The left-out photo's preview isn't needed any more.
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:two.jpg");
      expect(URL.revokeObjectURL).not.toHaveBeenCalledWith("blob:one.jpg");
    });

    it("lets the guest add more photos before sharing", async () => {
      const { user } = await renderOpenEvent();
      await user.upload(cameraInput(), jpeg("first.jpg"));
      const dialog = await screen.findByRole("dialog");

      await user.click(within(dialog).getByRole("button", { name: "Add photos" }));
      await user.upload(within(dialog).getByLabelText("Add photos from your gallery"), [jpeg("second.jpg"), jpeg("third.jpg")]);

      expect(screen.getByRole("dialog", { name: "Share these 3 photos on the live wall?" })).toBe(dialog);
      expect(tileSrc(dialog, 3)).toBe("blob:third.jpg");
      await user.click(within(dialog).getByRole("button", { name: "Share 3 photos" }));
      // Two upload at a time; all three are queued.
      const grid = await screen.findByRole("list", { name: "Your photos" });
      expect(within(grid).getAllByRole("listitem")).toHaveLength(3);
    });

    it("releases the previews if the page goes away before the guest answers", async () => {
      const { user, view } = await renderOpenEvent();
      await user.upload(galleryInput(), [jpeg("one.jpg")]);
      await screen.findByRole("dialog");

      view.unmount();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:one.jpg");
      expect(uploadPhoto).not.toHaveBeenCalled();
    });
  });

  it("queues and uploads selected photos with the guest's name and message", async () => {
    const { user } = await renderOpenEvent();
    await user.type(screen.getByLabelText(/your name/i), "Aisyah");
    await user.type(screen.getByLabelText(/message/i), "Congrats!");

    const files = [jpeg("one.jpg"), jpeg("two.jpg")];
    await share(user, galleryInput(), files);

    await waitFor(() => expect(uploadPhoto).toHaveBeenCalledTimes(2));
    expect(preparePhoto).toHaveBeenCalledWith(files[0], {
      maxBytes: DEFAULT_CONFIG.max_upload_bytes,
      allowedTypes: DEFAULT_CONFIG.allowed_mime_types,
    });
    expect(uploadPhoto).toHaveBeenCalledWith(
      EVENT_ID,
      files[0],
      "one.jpg",
      expect.objectContaining({ uploaderName: "Aisyah", caption: "Congrats!" }),
    );
    expect(uploadPhoto).toHaveBeenCalledWith(
      EVENT_ID,
      files[1],
      "two.jpg",
      expect.objectContaining({ uploaderName: "Aisyah", caption: "Congrats!" }),
    );
    // The message belonged to that batch; the name is kept for next time.
    expect(screen.getByLabelText(/message/i)).toHaveValue("");
    expect(screen.getByLabelText(/your name/i)).toHaveValue("Aisyah");

    const grid = screen.getByRole("list", { name: "Your photos" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("0 of 2 photos uploaded")).toBeInTheDocument();

    act(() => upload(0).options.onProgress?.(0.5));
    expect(screen.getByRole("progressbar", { name: "Uploading photo 1" })).toHaveAttribute("aria-valuenow", "50");

    await act(async () => upload(0).resolve(photo("p1")));
    expect(await screen.findByText("1 of 2 photos uploaded")).toBeInTheDocument();
    expect(within(grid).getAllByText("Uploaded")).toHaveLength(1);

    await act(async () => upload(1).resolve(photo("p2")));
    expect(await screen.findByRole("heading", { name: "Your photos are on the wall!" })).toBeInTheDocument();
    expect(screen.getByText("2 of 2 photos uploaded. Your photos are on the wall!")).toBeInTheDocument();
    expect(screen.getByText(/You've shared 2 photos/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See the live wall" })).toHaveAttribute("href", `/e/${EVENT_ID}/wall`);
    expect(within(grid).getAllByText("Uploaded")).toHaveLength(2);
    expect(screen.getByText("12 photos on the wall")).toBeInTheDocument();
  });

  it("shows an upload error and lets the guest retry", async () => {
    const { user } = await renderOpenEvent();
    await share(user, cameraInput(), jpeg("party.jpg"));

    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => upload(0).reject(networkError("The upload was interrupted. Check your connection and try again.")));

    expect(await screen.findByText("The upload was interrupted. Check your connection and try again.")).toBeInTheDocument();
    expect(screen.getByText("0 of 1 photo uploaded, 1 not sent")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Some photos weren't sent" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry photo 1" }));
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(preparePhoto).toHaveBeenCalledTimes(1);
    await act(async () => upload(1).resolve(photo("p1")));

    expect(await screen.findByRole("heading", { name: "Your photos are on the wall!" })).toBeInTheDocument();
    expect(screen.queryByText(/The upload was interrupted/)).not.toBeInTheDocument();
  });

  it("lets the guest remove a photo that can't be uploaded", async () => {
    vi.mocked(uploadPhoto).mockRejectedValueOnce(
      new ApiError({ status: 415, code: "UNSUPPORTED_MEDIA_TYPE", message: "Please choose a JPEG, PNG or WebP image." }),
    );
    const { user } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("odd.jpg")]);

    expect(await screen.findByText("Please choose a JPEG, PNG or WebP image.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry photo 1" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove photo 1" }));
    expect(screen.queryByRole("list", { name: "Your photos" })).not.toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:odd.jpg");
  });

  it("switches to the closed screen when an upload finds uploads closed, and says so", async () => {
    vi.mocked(uploadPhoto).mockRejectedValueOnce(
      new ApiError({ status: 403, code: "UPLOADS_CLOSED", message: "Uploads are closed." }),
    );
    const { user } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("late.jpg")]);

    const heading = await screen.findByRole("heading", { level: 1, name: "The host has closed uploads" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText("Photos that hadn't finished sending weren't added.")).toBeInTheDocument();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:late.jpg");
  });

  it("switches to the not-found screen when the event is deleted mid-upload", async () => {
    vi.mocked(uploadPhoto).mockRejectedValueOnce(
      new ApiError({ status: 404, code: "EVENT_NOT_FOUND", message: "Event not found." }),
    );
    const { user } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("late.jpg")]);

    const heading = await screen.findByRole("heading", { level: 1, name: "Event not found" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByRole("alert")).toHaveTextContent("This event is no longer available");
  });

  it("switches to a full screen when the event reaches its photo limit mid-upload", async () => {
    vi.mocked(uploadPhoto).mockRejectedValueOnce(
      new ApiError({ status: 409, code: "EVENT_PHOTO_LIMIT", message: "This event is full." }),
    );
    const { user } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);

    const heading = await screen.findByRole("heading", { level: 1, name: "This event is full" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(screen.getByText(/The host's photo limit has been reached\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take a photo" })).not.toBeInTheDocument();
    // The other photo was stopped too, instead of being sent into the same error.
    expect(pending.every((item) => item.options.signal?.aborted)).toBe(true);

    // The host made room (the count is below the limit): back to the form.
    await user.click(screen.getByRole("button", { name: "Check again" }));
    const eventHeading = await screen.findByRole("heading", { level: 1, name: EVENT.name });
    await waitFor(() => expect(eventHeading).toHaveFocus());
    expect(screen.getByRole("button", { name: "Take a photo" })).toBeInTheDocument();
  });

  it("treats an event at its photo limit as full once the real limit is known", async () => {
    publicConfig.overrides = { max_photos_per_event: EVENT.photo_count };
    vi.mocked(api.getEvent).mockResolvedValue(EVENT);
    const user = userEvent.setup();
    renderPage();

    const heading = await screen.findByRole("heading", { level: 1, name: "This event is full" });
    // Shown straight away, not in place of a screen the guest was using: focus stays put.
    expect(heading).not.toHaveFocus();
    expect(screen.queryByRole("button", { name: "Take a photo" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText("This event is still full")).toBeInTheDocument();
  });

  it("doesn't judge the photo limit by the defaults before the config has loaded", async () => {
    publicConfig.loaded = false;
    publicConfig.overrides = { max_photos_per_event: EVENT.photo_count };
    await renderOpenEvent();

    expect(screen.getByRole("button", { name: "Take a photo" })).toBeInTheDocument();
  });

  it("brings the uploads into view after a pick", async () => {
    const { user } = await renderOpenEvent();
    await share(user, cameraInput(), jpeg("party.jpg"));

    const uploads = screen.getByRole("region", { name: "Your uploads" });
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(scrollIntoView.mock.contexts[0]).toBe(uploads);
  });

  it("scrolls without animation when the guest prefers reduced motion", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(prefers-reduced-motion: reduce)" }));
    const { user } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("one.jpg")]);

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "start" });
  });

  it("keeps uploading when the guest leaves the page, and shows the queue again on return", async () => {
    const { user, view } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(unloadIsGuarded()).toBe(true);

    // e.g. the Back gesture or the Wall link: leaving doesn't cancel anything...
    view.unmount();
    expect(pending.some((item) => item.options.signal?.aborted)).toBe(false);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    // ...but closing the tab would, so that still asks first.
    expect(unloadIsGuarded()).toBe(true);
    await act(async () => upload(0).resolve(photo("p1")));

    renderPage();
    await screen.findByRole("heading", { level: 1, name: EVENT.name });
    const grid = screen.getByRole("list", { name: "Your photos" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(2);
    expect(within(grid).getAllByText("Uploaded")).toHaveLength(1);
    expect(screen.getByText("1 of 2 photos uploaded")).toBeInTheDocument();

    await act(async () => upload(1).resolve(photo("p2")));
    expect(await screen.findByRole("heading", { name: "Your photos are on the wall!" })).toBeInTheDocument();
    expect(uploadPhoto).toHaveBeenCalledTimes(2);
  });

  it("releases the queue once uploads left running in the background have settled", async () => {
    const { user, view } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);
    await waitFor(() => expect(pending).toHaveLength(2));
    view.unmount();

    await act(async () => upload(0).resolve(photo("p1")));
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    await act(async () => upload(1).reject(networkError()));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:one.jpg");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:two.jpg");
    expect(unloadIsGuarded()).toBe(false);

    renderPage();
    await screen.findByRole("heading", { level: 1, name: EVENT.name });
    expect(screen.queryByRole("list", { name: "Your photos" })).not.toBeInTheDocument();
  });

  it("releases a finished queue as soon as the guest leaves", async () => {
    const { user, view } = await renderOpenEvent();
    await share(user, galleryInput(), [jpeg("one.jpg")]);
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => upload(0).resolve(photo("p1")));
    await screen.findByRole("heading", { name: "Your photos are on the wall!" });

    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:one.jpg");
    expect(unloadIsGuarded()).toBe(false);
  });

  it("caps one selection at 20 photos and can cancel a photo in progress", async () => {
    const { user } = await renderOpenEvent();
    const files = Array.from({ length: 23 }, (_, index) => jpeg(`p${index + 1}.jpg`));
    await user.upload(galleryInput(), files);

    const dialog = await screen.findByRole("dialog", { name: "Share these 20 photos on the live wall?" });
    expect(within(dialog).getByText("Showing the first 20 photos")).toBeInTheDocument();
    expect(within(dialog).getByText("You picked 23. Send the rest in another go.")).toBeInTheDocument();
    expect(within(within(dialog).getByRole("list", { name: "Photos to share" })).getAllByRole("listitem")).toHaveLength(20);
    // No room for more in this go.
    expect(within(dialog).queryByRole("button", { name: /^Add/ })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Share 20 photos" }));

    const grid = await screen.findByRole("list", { name: "Your photos" });
    expect(within(grid).getAllByRole("listitem")).toHaveLength(20);
    await waitFor(() => expect(pending).toHaveLength(2));

    await user.click(screen.getByRole("button", { name: "Cancel photo 1" }));
    expect(upload(0).options.signal?.aborted).toBe(true);
    expect(within(grid).getAllByRole("listitem")).toHaveLength(19);
    await waitFor(() => expect(pending).toHaveLength(3));
  });

  describe("with a per-guest photo limit", () => {
    /** This browser shared photos to the event before (it kept a guest key). */
    function returningGuest() {
      window.localStorage.setItem(`gk.guest.${EVENT_ID}`, "k".repeat(43));
    }

    function limitTo(limit: number, used = 0) {
      publicConfig.overrides = { max_photos_per_guest: limit };
      vi.mocked(api.getGuestQuota).mockResolvedValue({ limit, used, remaining: Math.max(0, limit - used) });
      if (used > 0) returningGuest();
    }

    const allShared = () => screen.findByRole("heading", { level: 1, name: "You've shared all your photos" });

    it("shows nothing extra when guests have no limit", async () => {
      await renderOpenEvent();
      expect(api.getGuestQuota).not.toHaveBeenCalled();
      expect(screen.queryByText(/you can share/i)).not.toBeInTheDocument();
    });

    it("starts a first-time guest from the configured limit, counting down as photos arrive", async () => {
      limitTo(3);
      const { user } = await renderOpenEvent();
      expect(screen.getByText("You can share up to 3 photos")).toBeInTheDocument();
      // Nothing shared here yet: no need to ask the server.
      expect(api.getGuestQuota).not.toHaveBeenCalled();

      await share(user, galleryInput(), [jpeg("one.jpg")]);
      await waitFor(() => expect(pending).toHaveLength(1));
      await act(async () => upload(0).resolve(photo("p1")));
      expect(await screen.findByText("You can share 2 more photos")).toBeInTheDocument();
    });

    it("asks the server how many photos a returning guest has shared", async () => {
      limitTo(5, 2);
      await renderOpenEvent();
      expect(screen.getByText("You can share 3 more photos")).toBeInTheDocument();
      expect(api.getGuestQuota).toHaveBeenCalledWith(EVENT_ID, expect.any(AbortSignal));
    });

    it("asks right away, without waiting for the config", async () => {
      publicConfig.loaded = false;
      limitTo(5, 2);
      await renderOpenEvent();
      expect(screen.getByText("You can share 3 more photos")).toBeInTheDocument();
    });

    it("shows a loading screen, not the form, until a returning guest's count arrives", async () => {
      publicConfig.overrides = { max_photos_per_guest: 3 };
      returningGuest();
      let answer: (quota: GuestQuota) => void = () => undefined;
      vi.mocked(api.getGuestQuota).mockImplementation(() => new Promise((resolve) => (answer = resolve)));
      vi.mocked(api.getEvent).mockResolvedValue(EVENT);
      renderPage();

      // The event itself has loaded (its name is the tab title)...
      await waitFor(() => expect(document.title).toContain(EVENT.name));
      // ...but the form waits for the guest's count.
      expect(screen.getByText("Loading event…")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Take a photo" })).not.toBeInTheDocument();

      await act(async () => answer({ limit: 3, used: 3, remaining: 0 }));
      const heading = await allShared();
      // Shown straight after loading, not in place of a screen the guest was using: focus stays put.
      expect(heading).not.toHaveFocus();
    });

    it("says on the gallery button how many photos fit, before the guest picks", async () => {
      limitTo(5, 3);
      await renderOpenEvent();
      expect(screen.getByRole("button", { name: "Choose up to 2 photos" })).toBeInTheDocument();
      expect(galleryInput()).toHaveAttribute("multiple");
    });

    it("lets the gallery pick only one photo when one is left", async () => {
      limitTo(5, 4);
      await renderOpenEvent();
      expect(screen.getByRole("button", { name: "Choose 1 photo" })).toBeInTheDocument();
      expect(galleryInput()).not.toHaveAttribute("multiple");
    });

    it("lets a guest who picked too many choose which photos to share", async () => {
      limitTo(5, 3);
      const { user } = await renderOpenEvent();
      expect(screen.getByText("You can share 2 more photos")).toBeInTheDocument();

      const files = [jpeg("a.jpg"), jpeg("b.jpg"), jpeg("c.jpg"), jpeg("d.jpg")];
      await user.upload(galleryInput(), files);

      // Every pick is shown; the first ones that fit are chosen.
      const dialog = await screen.findByRole("dialog", { name: "Share these 2 photos on the live wall?" });
      expect(within(dialog).getByText("You can share 2 more photos")).toBeInTheDocument();
      expect(within(dialog).getByText("You picked 4. Tap the photos to choose which 2 to share.")).toBeInTheDocument();
      expect(within(dialog).getByText("2 of 2 chosen")).toBeInTheDocument();
      const tile = (number: number) => within(dialog).getByRole("button", { name: `Share photo ${number}` });
      expect([1, 2, 3, 4].map((number) => tile(number).getAttribute("aria-pressed"))).toEqual(["true", "true", "false", "false"]);
      expect(within(dialog).queryByRole("button", { name: /^Add/ })).not.toBeInTheDocument();

      // Full: a third one is turned down until one comes out.
      await user.click(tile(3));
      expect(within(dialog).getByText("You can share up to 2 photos. Tap a chosen photo to take it out first.")).toBeInTheDocument();
      expect(tile(3)).toHaveAttribute("aria-pressed", "false");

      await user.click(tile(1));
      expect(within(dialog).getByText("1 of 2 chosen")).toBeInTheDocument();
      await user.click(tile(3));
      expect(within(dialog).getByText("2 of 2 chosen")).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Share 2 photos" }));

      await waitFor(() => expect(uploadPhoto).toHaveBeenCalledTimes(2));
      expect(uploadPhoto).toHaveBeenCalledWith(EVENT_ID, files[1], "b.jpg", expect.anything());
      expect(uploadPhoto).toHaveBeenCalledWith(EVENT_ID, files[2], "c.jpg", expect.anything());
      const grid = screen.getByRole("list", { name: "Your photos" });
      expect(within(grid).getAllByRole("listitem")).toHaveLength(2);
    });

    it("offers to add a photo only while the guest has room", async () => {
      limitTo(5, 2);
      const { user } = await renderOpenEvent();
      await user.upload(galleryInput(), [jpeg("a.jpg"), jpeg("b.jpg")]);

      const dialog = await screen.findByRole("dialog", { name: "Share these 2 photos on the live wall?" });
      expect(within(dialog).getByText("2 of 3 chosen")).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Add a photo" }));
      const addInput = within(dialog).getByLabelText<HTMLInputElement>("Add photos from your gallery");
      expect(addInput).not.toHaveAttribute("multiple");
      await user.upload(addInput, jpeg("c.jpg"));

      expect(within(dialog).getByText("3 of 3 chosen")).toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: /^Add/ })).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Share 3 photos" }));
      // Two upload at a time; all three are queued.
      const grid = await screen.findByRole("list", { name: "Your photos" });
      expect(within(grid).getAllByRole("listitem")).toHaveLength(3);
    });

    it("holds the pickers while the last allowed photo is sending, so no camera shot is lost", async () => {
      limitTo(5, 4);
      const { user } = await renderOpenEvent();
      expect(screen.getByText("You can share 1 more photo")).toBeInTheDocument();
      await share(user, cameraInput(), jpeg("one.jpg"));
      await waitFor(() => expect(pending).toHaveLength(1));

      expect(screen.getByText("Your last photo is on its way")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Take a photo" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Choose from gallery" })).toBeDisabled();

      // It didn't make it: the slot is free again.
      await act(async () => upload(0).reject(networkError()));
      expect(await screen.findByText("You can share 1 more photo")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Take a photo" })).toBeEnabled();
    });

    it("refuses a pick that arrives while every remaining photo is sending", async () => {
      limitTo(5, 4);
      const { user } = await renderOpenEvent();
      await share(user, cameraInput(), jpeg("one.jpg"));
      await waitFor(() => expect(pending).toHaveLength(1));

      // The inputs themselves stay usable (e.g. a picker opened just before): the pick is turned down.
      await user.upload(galleryInput(), [jpeg("two.jpg")]);
      expect(await screen.findByText("You've picked all the photos you can share")).toBeInTheDocument();
      const grid = screen.getByRole("list", { name: "Your photos" });
      expect(within(grid).getAllByRole("listitem")).toHaveLength(1);
      expect(preparePhoto).toHaveBeenCalledTimes(1);
    });

    it("says how many photos fit when the allowance and the 20-photo cap meet", async () => {
      limitTo(20);
      const { user } = await renderOpenEvent();
      await user.upload(galleryInput(), Array.from({ length: 25 }, (_, index) => jpeg(`p${index + 1}.jpg`)));
      const dialog = await screen.findByRole("dialog", { name: "Share these 20 photos on the live wall?" });
      expect(within(dialog).getByText("You can share up to 20 photos")).toBeInTheDocument();
      expect(
        within(dialog).getByText("You picked 25. Tap the photos to choose which 20 to share. Only the first 20 are shown."),
      ).toBeInTheDocument();
    });

    it("tells a guest with a big allowance how many more fit after the first 20", async () => {
      limitTo(30);
      const { user } = await renderOpenEvent();
      await user.upload(galleryInput(), Array.from({ length: 25 }, (_, index) => jpeg(`p${index + 1}.jpg`)));
      const dialog = await screen.findByRole("dialog", { name: "Share these 20 photos on the live wall?" });
      expect(within(dialog).getByText("Showing the first 20 photos")).toBeInTheDocument();
      expect(within(dialog).getByText("You picked 25. You can send 10 more photos after these.")).toBeInTheDocument();
    });

    it("counts a returning guest's photos that were still sending when they left the page", async () => {
      limitTo(3);
      const { user, view } = await renderOpenEvent();
      await share(user, galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);
      await waitFor(() => expect(pending).toHaveLength(2));
      view.unmount();

      // Back from the wall: this browser now has a key, so the count is asked for.
      returningGuest();
      let answer: (quota: GuestQuota) => void = () => undefined;
      vi.mocked(api.getGuestQuota).mockImplementation(() => new Promise((resolve) => (answer = resolve)));
      renderPage();
      await act(async () => upload(0).resolve(photo("p1")));
      await act(async () => answer({ limit: 3, used: 1, remaining: 2 }));

      // One shared, one still sending: one more fits.
      expect(await screen.findByText("You can share 1 more photo")).toBeInTheDocument();
      await act(async () => upload(1).resolve(photo("p2")));
      expect(screen.getByText("You can share 1 more photo")).toBeInTheDocument();
    });

    it("tells a guest who comes back after sharing all their photos", async () => {
      limitTo(3, 3);
      vi.mocked(api.getEvent).mockResolvedValue(EVENT);
      renderPage();
      const heading = await allShared();

      expect(heading).not.toHaveFocus();
      expect(screen.getByText(/Each guest can share up to 3 photos at/)).toHaveTextContent(EVENT.name);
      expect(screen.getByRole("link", { name: "View the photo wall" })).toHaveAttribute("href", `/e/${EVENT_ID}/wall`);
      expect(screen.queryByRole("button", { name: "Take a photo" })).not.toBeInTheDocument();
    });

    it("lets the guest check again after the host deleted one of their photos", async () => {
      limitTo(3, 3);
      vi.mocked(api.getEvent).mockResolvedValue(EVENT);
      const user = userEvent.setup();
      renderPage();
      await allShared();

      await user.click(screen.getByRole("button", { name: "Check again" }));
      expect(await screen.findByText("You've still shared all your photos")).toBeInTheDocument();
      expect(screen.getByText("If the host removes one of yours, you can share another.")).toBeInTheDocument();

      vi.mocked(api.getGuestQuota).mockResolvedValue({ limit: 3, used: 2, remaining: 1 });
      await user.click(screen.getByRole("button", { name: "Check again" }));
      const heading = await screen.findByRole("heading", { level: 1, name: EVENT.name });
      await waitFor(() => expect(heading).toHaveFocus());
      expect(screen.getByText("You can share 1 more photo")).toBeInTheDocument();
    });

    it("lets a guest turned away by the server check again too", async () => {
      vi.mocked(uploadPhoto).mockRejectedValueOnce(
        new ApiError({ status: 409, code: "GUEST_PHOTO_LIMIT", message: "Limit.", details: { max_photos_per_guest: 2 } }),
      );
      vi.mocked(api.getGuestQuota).mockResolvedValue({ limit: 5, used: 2, remaining: 3 });
      const { user } = await renderOpenEvent();
      await share(user, galleryInput(), [jpeg("one.jpg")]);
      await allShared();

      // Meanwhile the host raised the limit.
      await user.click(screen.getByRole("button", { name: "Check again" }));
      expect(await screen.findByText("You can share 3 more photos")).toBeInTheDocument();
    });

    it("thanks the guest once their last allowed photo is on the wall", async () => {
      limitTo(2, 1);
      const { user } = await renderOpenEvent();
      expect(screen.getByText("You can share 1 more photo")).toBeInTheDocument();
      await share(user, galleryInput(), [jpeg("last.jpg")]);
      await waitFor(() => expect(pending).toHaveLength(1));
      await act(async () => upload(0).resolve(photo("p1")));

      const heading = await allShared();
      await waitFor(() => expect(heading).toHaveFocus());
      expect(screen.getByText("Thank you! Your 1 photo made it to the wall.")).toBeInTheDocument();
    });

    it("switches to the all-shared screen when the server turns a photo away", async () => {
      // The allowance wasn't known (no limit in the config yet): the server's refusal decides.
      vi.mocked(uploadPhoto).mockRejectedValueOnce(
        new ApiError({
          status: 409,
          code: "GUEST_PHOTO_LIMIT",
          message: "Each guest can share up to 4 photos at this event.",
          details: { max_photos_per_guest: 4 },
        }),
      );
      const { user } = await renderOpenEvent();
      await share(user, galleryInput(), [jpeg("one.jpg"), jpeg("two.jpg")]);

      const heading = await allShared();
      await waitFor(() => expect(heading).toHaveFocus());
      expect(screen.getByText(/Each guest can share up to 4 photos at/)).toBeInTheDocument();
      expect(screen.getByText("Photos that hadn't finished sending weren't added.")).toBeInTheDocument();
      expect(pending.every((item) => item.options.signal?.aborted)).toBe(true);
    });

    it("words the all-shared screen without a number when the server didn't give one", async () => {
      vi.mocked(uploadPhoto).mockRejectedValueOnce(
        new ApiError({ status: 409, code: "GUEST_PHOTO_LIMIT", message: "Limit reached." }),
      );
      const { user } = await renderOpenEvent();
      await share(user, galleryInput(), [jpeg("one.jpg")]);

      await allShared();
      expect(screen.getByText(/You've reached the photo limit for each guest at/)).toBeInTheDocument();
      expect(screen.queryByText(/0 photos/)).not.toBeInTheDocument();
    });

    it("keeps the form, without a count, when a returning guest's count can't be loaded", async () => {
      publicConfig.overrides = { max_photos_per_guest: 3 };
      returningGuest();
      vi.mocked(api.getGuestQuota).mockRejectedValue(networkError());
      await renderOpenEvent();

      expect(screen.getByRole("button", { name: "Take a photo" })).toBeInTheDocument();
      expect(screen.queryByText(/you can share/i)).not.toBeInTheDocument();
    });

    it("shows no count to a returning guest when the server has no limit", async () => {
      returningGuest();
      vi.mocked(api.getGuestQuota).mockResolvedValue({ limit: null, used: 2, remaining: null });
      await renderOpenEvent();
      expect(screen.queryByText(/you can share/i)).not.toBeInTheDocument();
    });
  });

  it("remembers the guest's name", async () => {
    guestName.set("Budi");
    const { user, view } = await renderOpenEvent();

    const nameField = screen.getByLabelText(/your name/i);
    expect(nameField).toHaveValue("Budi");

    await user.clear(nameField);
    await user.type(nameField, "Siti");
    expect(guestName.get()).toBe("Siti");

    view.unmount();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: EVENT.name });
    expect(screen.getByLabelText(/your name/i)).toHaveValue("Siti");
  });
});
