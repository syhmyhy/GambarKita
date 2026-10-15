import { CirclePause } from "lucide-react";

import { EventQrCode } from "../EventQrCode";
import { displayUrl } from "./photoText";

/** Corner card inviting guests in the room to join (TV / projector). */
export function QrPanel({ url, eventName, uploadsOpen }: { url: string; eventName: string; uploadsOpen: boolean }) {
  return (
    <aside
      aria-label="Join this wall"
      className="fixed bottom-[max(1rem,env(safe-area-inset-bottom))] right-[max(1rem,env(safe-area-inset-right))] z-20 flex w-56 animate-slide-up flex-col items-center rounded-[var(--radius-card)] bg-stone-900/90 p-4 text-center text-white shadow-2xl shadow-black/50 ring-1 ring-white/10 backdrop-blur-md 2xl:w-64"
    >
      {uploadsOpen ? (
        <>
          <p className="text-sm font-bold leading-snug text-balance 2xl:text-base">Scan to share your photos</p>
          <EventQrCode url={url} eventName={eventName} size={140} showActions={false} className="mt-3 2xl:[&_svg]:size-44" />
          <p className="mt-2 text-xs break-all text-white/60 2xl:text-sm">{displayUrl(url)}</p>
        </>
      ) : (
        <>
          <CirclePause className="size-8 text-brand-300" aria-hidden="true" />
          <p className="mt-2 text-sm font-bold">Uploads are paused</p>
          <p className="mt-1 text-xs text-white/60">The host isn't accepting new photos right now.</p>
        </>
      )}
    </aside>
  );
}
