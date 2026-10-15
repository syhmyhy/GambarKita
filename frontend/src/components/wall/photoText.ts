import type { Photo } from "../../lib/types";

/** Alt text for a guest photo, built from its caption and who shared it. */
export function describePhoto(photo: Photo): string {
  const name = photo.uploader_name?.trim();
  const caption = photo.caption?.trim();
  if (name && caption) return `${caption} (photo by ${name})`;
  if (caption) return caption;
  if (name) return `Photo by ${name}`;
  return "Guest photo";
}

/** "gambarkita.example/e/abc" for showing under a QR code. */
export function displayUrl(url: string): string {
  return url.replace(/^https?:\/\//, "").replace(/\/$/, "");
}
