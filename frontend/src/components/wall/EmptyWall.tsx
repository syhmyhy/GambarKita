import { Camera, CirclePause } from "lucide-react";

import { EventQrCode } from "../EventQrCode";
import { ButtonLink } from "../ui/Button";
import { displayUrl } from "./photoText";

interface EmptyWallProps {
  uploadUrl: string;
  uploadHref: string;
  eventName: string;
  uploadsOpen: boolean;
}

/** No photos yet: the QR code is the hero until the first one arrives. */
export function EmptyWall({ uploadUrl, uploadHref, eventName, uploadsOpen }: EmptyWallProps) {
  return (
    <div className="flex min-h-[calc(100dvh-7rem)] animate-fade-in flex-col items-center justify-center px-4 pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-10 text-center">
      {uploadsOpen ? (
        <>
          <h2 className="text-3xl font-extrabold tracking-tight text-balance sm:text-4xl 2xl:text-5xl">Scan to share the first photo</h2>
          <p className="mt-3 text-lg text-white/70 text-pretty">Point your phone's camera at the code. No app or sign-up needed.</p>
          <EventQrCode url={uploadUrl} eventName={eventName} size={260} showActions={false} className="mt-8 2xl:[&_svg]:size-80" />
          <p className="mt-4 max-w-full font-semibold break-all text-white/80 sm:text-lg">{displayUrl(uploadUrl)}</p>
          <ButtonLink to={uploadHref} size="lg" className="mt-6 lg:hidden" icon={<Camera className="size-5" aria-hidden="true" />}>
            Add the first photo
          </ButtonLink>
        </>
      ) : (
        <>
          <span className="grid size-16 place-items-center rounded-2xl bg-white/10 text-brand-300">
            <CirclePause className="size-8" aria-hidden="true" />
          </span>
          <h2 className="mt-5 text-3xl font-extrabold tracking-tight text-balance sm:text-4xl">Uploads are paused</h2>
          <p className="mt-3 text-lg text-white/70 text-pretty">Photos will appear here as soon as the host opens uploads.</p>
        </>
      )}
      <p role="status" className="mt-10 inline-flex animate-pulse items-center gap-2.5 font-medium text-white/60">
        <span className="size-2 rounded-full bg-brand-400" aria-hidden="true" />
        Waiting for photos…
      </p>
    </div>
  );
}
