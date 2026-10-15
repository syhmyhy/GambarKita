// Mirrors backend/app/schemas.py. Keep the two in sync.

export interface EventLinks {
  upload: string;
  wall: string;
}

export interface EventPublic {
  id: string;
  name: string;
  uploads_open: boolean;
  photo_count: number;
  created_at: string;
  links: EventLinks;
}

export interface EventCreated {
  event: EventPublic;
  host_token: string;
  host_url: string;
}

export interface Photo {
  id: string;
  event_id: string;
  url: string;
  thumbnail_url: string;
  content_type: string;
  width: number;
  height: number;
  size_bytes: number;
  uploader_name: string | null;
  caption: string | null;
  created_at: string;
}

export interface PhotoPage {
  items: Photo[];
  next_cursor: string | null;
}

export interface GuestQuota {
  /** Photos each guest may share to the event; null when there is no limit. */
  limit: number | null;
  used: number;
  remaining: number | null;
}

export interface PublicConfig {
  max_upload_bytes: number;
  allowed_mime_types: string[];
  max_photos_per_event: number;
  /** null: guests have no individual limit. */
  max_photos_per_guest: number | null;
  max_event_name_length: number;
  max_uploader_name_length: number;
  max_caption_length: number;
  event_creation_requires_key: boolean;
}

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details?: unknown;
    request_id?: string;
  };
}

/** Messages pushed over /ws/events/{id}. */
export type LiveMessage =
  | { type: "hello"; event_id: string; data: EventPublic }
  | { type: "photo.created"; event_id: string; data: Photo }
  | { type: "photo.deleted"; event_id: string; data: { id: string } }
  | { type: "event.updated"; event_id: string; data: EventPublic }
  | { type: "event.deleted"; event_id: string }
  | { type: "pong" };
