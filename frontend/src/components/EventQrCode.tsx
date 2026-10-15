import { Download, Printer } from "lucide-react";
import { QRCodeCanvas, QRCodeSVG } from "qrcode.react";
import { useRef } from "react";

import { Button } from "./ui/Button";
import { cn } from "./ui/cn";

interface EventQrCodeProps {
  /** The absolute URL guests should open (the event's upload page). */
  url: string;
  /** Used for the downloaded file name and the printable card. */
  eventName: string;
  size?: number;
  className?: string;
  showActions?: boolean;
}

function safeFileName(name: string): string {
  return (name.normalize("NFKD").replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-").toLowerCase() || "event").slice(0, 60);
}

/**
 * The event's QR code, with PNG download and a print-friendly card. Always
 * rendered dark-on-white for reliable scanning, even in dark mode.
 */
export function EventQrCode({ url, eventName, size = 220, className, showActions = true }: EventQrCodeProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const download = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const link = document.createElement("a");
    link.download = `${safeFileName(eventName)}-qr.png`;
    link.href = canvas.toDataURL("image/png");
    link.click();
  };

  const print = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // A hidden same-origin iframe is used instead of a popup (no popup blockers).
    const frame = document.createElement("iframe");
    frame.setAttribute("aria-hidden", "true");
    frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
    document.body.appendChild(frame);
    const doc = frame.contentDocument;
    const view = frame.contentWindow;
    if (!doc || !view) {
      frame.remove();
      return;
    }
    doc.title = `${eventName} – QR code`;
    const style = doc.createElement("style");
    style.textContent =
      "body{font-family:system-ui,sans-serif;text-align:center;padding:48px;color:#1c1917}" +
      "h1{font-size:28px;margin:0 0 8px}p{font-size:18px;margin:0 0 32px;color:#57534e}" +
      "img{width:360px;height:360px}small{display:block;margin-top:24px;color:#78716c;word-break:break-all}";
    doc.head.appendChild(style);
    const heading = doc.createElement("h1");
    heading.textContent = eventName; // textContent: never interpret the name as HTML
    const lead = doc.createElement("p");
    lead.textContent = "Scan to share your photos";
    const image = doc.createElement("img");
    image.alt = "QR code";
    const link = doc.createElement("small");
    link.textContent = url;
    doc.body.append(heading, lead, image, link);
    image.onload = () => {
      view.focus();
      view.print();
      window.setTimeout(() => frame.remove(), 1000);
    };
    image.src = canvas.toDataURL("image/png");
  };

  return (
    <div className={cn("flex flex-col items-center", className)}>
      <div className="rounded-2xl bg-white p-4 shadow-sm ring-1 ring-stone-200">
        <QRCodeSVG
          value={url}
          size={size}
          level="M"
          marginSize={0}
          bgColor="#ffffff"
          fgColor="#1c1917"
          role="img"
          aria-label={`QR code linking to ${url}`}
        />
      </div>
      {/* High-resolution copy for download and printing. */}
      <QRCodeCanvas ref={canvasRef} value={url} size={1024} level="M" marginSize={4} className="hidden" aria-hidden="true" />
      {showActions && (
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Button variant="secondary" icon={<Download className="size-4" aria-hidden="true" />} onClick={download}>
            Download PNG
          </Button>
          <Button variant="secondary" icon={<Printer className="size-4" aria-hidden="true" />} onClick={print}>
            Print
          </Button>
        </div>
      )}
    </div>
  );
}
