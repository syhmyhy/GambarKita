import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import type { LiveFeed } from "../../hooks/useLiveFeed";
import { guestUploadUrl, routes } from "../../lib/env";
import type { EventPublic } from "../../lib/types";
import { cn } from "../ui/cn";
import { useToast } from "../ui/Toast";
import { EmptyWall } from "./EmptyWall";
import { Lightbox } from "./Lightbox";
import { PhotoGrid } from "./PhotoGrid";
import { QrPanel } from "./QrPanel";
import { Slideshow } from "./Slideshow";
import { useFullscreen } from "./useFullscreen";
import { useIdle } from "./useIdle";
import { useMediaQuery } from "./useMediaQuery";
import { type WallMode, WallTopBar } from "./WallTopBar";

const AUTO_HIDE_MS = 3000;
const LARGE_SCREEN = "(min-width: 64rem)"; // Tailwind's lg

interface LiveWallProps {
  eventId: string;
  event: EventPublic;
  feed: LiveFeed;
}

/** The loaded wall: top bar, grid or slideshow, QR panel and lightbox. */
export function LiveWall({ eventId, event, feed }: LiveWallProps) {
  const { photos, hasMore, loadingMore, loadMore, latestLiveId } = feed;
  const toast = useToast();
  // ?view=slideshow lets a TV or projector open straight into the slideshow.
  const [searchParams, setSearchParams] = useSearchParams();
  const mode: WallMode = searchParams.get("view") === "slideshow" ? "slideshow" : "grid";
  const [paused, setPaused] = useState(false);
  const [openPhotoId, setOpenPhotoId] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // null = follow the screen size (shown on large screens) until the viewer toggles it.
  const [qrChoice, setQrChoice] = useState<boolean | null>(null);
  const isLargeScreen = useMediaQuery(LARGE_SCREEN);
  const fullscreen = useFullscreen();
  const idle = useIdle(AUTO_HIDE_MS, mode === "slideshow" || fullscreen.active);

  const empty = photos.length === 0;
  const slideshow = mode === "slideshow" && !empty;
  const qrOpen = qrChoice ?? isLargeScreen;
  const uploadPath = routes.upload(eventId);
  // What the QR codes encode: a link that works on guests' phones.
  const uploadUrl = guestUploadUrl(event);
  const total = Math.max(event.photo_count, photos.length);
  const openPhoto = openPhotoId !== null && !slideshow ? photos.find((photo) => photo.id === openPhotoId) : undefined;
  const lightboxOpen = openPhoto !== undefined;

  // A toast would sit underneath the lightbox (a modal dialog in the top layer),
  // so while it is open the lightbox shows the error itself.
  const lightboxOpenRef = useRef(lightboxOpen);
  useEffect(() => {
    lightboxOpenRef.current = lightboxOpen;
  }, [lightboxOpen]);

  const loadOlder = useCallback(() => {
    loadMore().then(
      () => setLoadFailed(false),
      (error: unknown) => {
        setLoadFailed(true);
        if (!lightboxOpenRef.current) toast.fromError(error);
      },
    );
  }, [loadMore, toast]);
  const autoLoad = hasMore && !loadFailed ? loadOlder : undefined;

  const toggleMode = () => {
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current);
        if (mode === "grid") params.set("view", "slideshow");
        else params.delete("view");
        return params;
      },
      { replace: true },
    );
  };

  const toggleFullscreen = () => {
    fullscreen.toggle().catch((error: unknown) => toast.fromError(error));
  };

  const latest = latestLiveId !== null ? photos.find((photo) => photo.id === latestLiveId) : undefined;

  return (
    <div className={cn("min-h-dvh bg-stone-950 text-white", idle && "cursor-none")}>
      <WallTopBar
        eventName={event.name}
        photoCount={total}
        status={feed.status}
        uploadHref={event.uploads_open ? uploadPath : null}
        mode={mode}
        onToggleMode={toggleMode}
        paused={paused}
        onTogglePause={slideshow ? () => setPaused((value) => !value) : undefined}
        fullscreen={fullscreen}
        onToggleFullscreen={toggleFullscreen}
        qrOpen={qrOpen}
        onToggleQr={empty ? undefined : () => setQrChoice(!qrOpen)}
        overlay={slideshow}
        hidden={idle}
      />

      <main id="main">
        {empty ? (
          <EmptyWall uploadUrl={uploadUrl} uploadHref={uploadPath} eventName={event.name} uploadsOpen={event.uploads_open} />
        ) : slideshow ? (
          <Slideshow
            photos={photos}
            total={total}
            latestLiveId={latestLiveId}
            paused={paused}
            onTogglePause={() => setPaused((value) => !value)}
            controlsHidden={idle}
            onNearEnd={autoLoad}
          />
        ) : (
          <PhotoGrid
            photos={photos}
            latestLiveId={latestLiveId}
            onOpen={setOpenPhotoId}
            hasMore={hasMore}
            loadingMore={loadingMore}
            loadFailed={loadFailed}
            onLoadMore={loadOlder}
            reserveCorner={qrOpen}
          />
        )}
      </main>

      {qrOpen && !empty && <QrPanel url={uploadUrl} eventName={event.name} uploadsOpen={event.uploads_open} />}

      {openPhoto && (
        <Lightbox
          photos={photos}
          photoId={openPhoto.id}
          total={total}
          onNavigate={setOpenPhotoId}
          onClose={() => setOpenPhotoId(null)}
          onNearEnd={autoLoad}
          loadFailed={hasMore && loadFailed}
          loadingMore={loadingMore}
          onRetry={loadOlder}
        />
      )}

      <p className="sr-only" aria-live="polite">
        {latest ? `New photo${latest.uploader_name ? ` from ${latest.uploader_name}` : ""}` : ""}
      </p>
    </div>
  );
}
