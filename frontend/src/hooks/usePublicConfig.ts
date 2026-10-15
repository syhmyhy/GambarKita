import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { PublicConfig } from "../lib/types";

/** Used until /api/config answers (and if it never does). Matches the backend defaults. */
export const DEFAULT_CONFIG: PublicConfig = {
  max_upload_bytes: 10 * 1024 * 1024,
  allowed_mime_types: ["image/jpeg", "image/png", "image/webp"],
  max_photos_per_event: 2000,
  max_photos_per_guest: null,
  max_event_name_length: 80,
  max_uploader_name_length: 40,
  max_caption_length: 140,
  event_creation_requires_key: false,
};

let cached: Promise<PublicConfig> | null = null;

function loadConfig(): Promise<PublicConfig> {
  cached ??= api.getConfig().catch((error: unknown) => {
    cached = null; // retry on the next mount
    throw error;
  });
  return cached;
}

/** The server's public limits (fetched once per page load, shared by all components). */
export function usePublicConfig(): { config: PublicConfig; loaded: boolean } {
  const [config, setConfig] = useState<PublicConfig | null>(null);

  useEffect(() => {
    let active = true;
    loadConfig().then(
      (value) => active && setConfig(value),
      () => undefined, // keep the defaults; the server re-validates everything anyway
    );
    return () => {
      active = false;
    };
  }, []);

  return { config: config ?? DEFAULT_CONFIG, loaded: config !== null };
}
