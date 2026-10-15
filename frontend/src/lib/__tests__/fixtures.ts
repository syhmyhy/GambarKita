import type { EventPublic, Photo } from "../types";

export const EVENT_ID = "abcdefghijkl";

/** ISO timestamp `minutes` after a fixed base time, so ordering is easy to read in tests. */
export function at(minutes: number): string {
  return new Date(Date.UTC(2026, 0, 1, 12, 0, 0) + minutes * 60_000).toISOString();
}

/** A 32-hex-digit media id derived from a short test id. */
export function hexFor(id: string): string {
  let hex = "";
  for (const char of id) hex += char.charCodeAt(0).toString(16);
  return hex.padStart(32, "0").slice(-32);
}

export function makePhoto(id: string, minutes: number, overrides: Partial<Photo> = {}): Photo {
  return {
    id,
    event_id: EVENT_ID,
    url: `/media/events/${EVENT_ID}/photos/${hexFor(id)}.jpg`,
    thumbnail_url: `/media/events/${EVENT_ID}/thumbs/${hexFor(id)}.webp`,
    content_type: "image/jpeg",
    width: 1600,
    height: 1200,
    size_bytes: 123_456,
    uploader_name: null,
    caption: null,
    created_at: at(minutes),
    ...overrides,
  };
}

export function makeEvent(overrides: Partial<EventPublic> = {}): EventPublic {
  return {
    id: EVENT_ID,
    name: "Aisyah & Daniel",
    uploads_open: true,
    photo_count: 0,
    created_at: at(0),
    links: { upload: `http://localhost/e/${EVENT_ID}`, wall: `http://localhost/e/${EVENT_ID}/wall` },
    ...overrides,
  };
}
