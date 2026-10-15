import { Camera, LayoutGrid, Maximize, Minimize, Pause, Play, Presentation, QrCode, ZoomIn, ZoomOut } from "lucide-react";

import type { ConnectionStatus } from "../../lib/realtime";
import { pluralize } from "../../lib/format";
import { ConnectionBadge } from "../ConnectionBadge";
import { LogoMark } from "../Logo";
import { ButtonLink, IconButton } from "../ui/Button";
import { cn } from "../ui/cn";
import type { Fullscreen } from "./useFullscreen";

export type WallMode = "grid" | "slideshow";

/** Grid zoom buttons: the same steps as pinching (bigger = fewer photos per row). */
export interface ZoomControls {
  onBigger: () => void;
  onSmaller: () => void;
  canBigger: boolean;
  canSmaller: boolean;
}

interface WallTopBarProps {
  eventName: string;
  photoCount: number;
  status: ConnectionStatus;
  /** Link to the upload page; null while uploads are closed. */
  uploadHref: string | null;
  mode: WallMode;
  onToggleMode: () => void;
  /** Only in slideshow mode. */
  paused?: boolean;
  onTogglePause?: () => void;
  fullscreen: Fullscreen;
  onToggleFullscreen: () => void;
  /** Omit to hide the QR toggle (e.g. when the empty state already shows a big QR code). */
  qrOpen?: boolean;
  onToggleQr?: () => void;
  /** Only for the grid. Shown from tablet size up; phones pinch instead. */
  zoom?: ZoomControls;
  /** Float over the photo instead of taking up space (slideshow). */
  overlay: boolean;
  /** Auto-hidden while nobody is interacting; focusing a control brings it back. */
  hidden: boolean;
}

export function WallTopBar({
  eventName,
  photoCount,
  status,
  uploadHref,
  mode,
  onToggleMode,
  paused,
  onTogglePause,
  fullscreen,
  onToggleFullscreen,
  qrOpen,
  onToggleQr,
  zoom,
  overlay,
  hidden,
}: WallTopBarProps) {
  return (
    <header
      className={cn(
        "inset-x-0 top-0 z-30 pl-[max(1rem,env(safe-area-inset-left))] pr-[max(1rem,env(safe-area-inset-right))] pt-[max(0.75rem,env(safe-area-inset-top))] transition duration-500",
        overlay
          ? "fixed bg-linear-to-b from-black/75 via-black/35 to-transparent pb-12"
          : "sticky border-b border-white/5 bg-stone-950/85 pb-3 backdrop-blur-lg",
        hidden &&
          "pointer-events-none -translate-y-3 opacity-0 focus-within:pointer-events-auto focus-within:translate-y-0 focus-within:opacity-100",
      )}
    >
      {/* Phones: name + status on the first row, count + controls on the second.
          Wider: logo | name over count | controls. */}
      <div className="mx-auto grid max-w-[120rem] grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
        <LogoMark className="hidden size-10 sm:row-span-2 sm:block" />
        <div className="col-span-2 flex min-w-0 items-center gap-2.5 sm:col-span-1">
          <h1 className="truncate text-lg font-extrabold tracking-tight sm:text-2xl">{eventName}</h1>
          <ConnectionBadge status={status} onDark className="shrink-0" />
        </div>
        <p className="truncate text-sm tabular-nums text-white/70 sm:col-start-2 sm:row-start-2 2xl:text-lg">{pluralize(photoCount, "photo")}</p>
        <div className="flex items-center justify-end gap-2 sm:col-start-3 sm:row-span-2 sm:row-start-1">
          {uploadHref && (
            <ButtonLink
              to={uploadHref}
              size="sm"
              className="h-11 sm:px-5"
              icon={<Camera className="size-4" aria-hidden="true" />}
            >
              <span className="sr-only sm:not-sr-only">Add photos</span>
            </ButtonLink>
          )}
          {zoom && (
            <div className="hidden items-center gap-2 sm:flex">
              <IconButton variant="light" label="Smaller photos" disabled={!zoom.canSmaller} onClick={zoom.onSmaller}>
                <ZoomOut className="size-5" aria-hidden="true" />
              </IconButton>
              <IconButton variant="light" label="Bigger photos" disabled={!zoom.canBigger} onClick={zoom.onBigger}>
                <ZoomIn className="size-5" aria-hidden="true" />
              </IconButton>
            </div>
          )}
          {onTogglePause && (
            <IconButton variant="light" label={paused ? "Resume slideshow" : "Pause slideshow"} onClick={onTogglePause}>
              {paused ? <Play className="size-5" aria-hidden="true" /> : <Pause className="size-5" aria-hidden="true" />}
            </IconButton>
          )}
          <IconButton
            variant="light"
            label={mode === "grid" ? "Switch to slideshow" : "Switch to grid"}
            onClick={onToggleMode}
          >
            {mode === "grid" ? <Presentation className="size-5" aria-hidden="true" /> : <LayoutGrid className="size-5" aria-hidden="true" />}
          </IconButton>
          {fullscreen.supported && (
            <IconButton
              variant="light"
              label={fullscreen.active ? "Exit full screen" : "Full screen"}
              onClick={onToggleFullscreen}
            >
              {fullscreen.active ? <Minimize className="size-5" aria-hidden="true" /> : <Maximize className="size-5" aria-hidden="true" />}
            </IconButton>
          )}
          {onToggleQr && (
            <IconButton
              variant="light"
              className={qrOpen ? "bg-white/25 ring-white/40" : undefined}
              label="QR code for guests"
              aria-pressed={qrOpen}
              onClick={onToggleQr}
            >
              <QrCode className="size-5" aria-hidden="true" />
            </IconButton>
          )}
        </div>
      </div>
    </header>
  );
}
