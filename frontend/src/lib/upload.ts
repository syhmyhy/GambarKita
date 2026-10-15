import { errorFromResponse, networkError } from "./api";
import { apiUrl } from "./env";
import { guestKeys } from "./storage";
import type { Photo } from "./types";

export interface UploadOptions {
  uploaderName?: string;
  caption?: string;
  /** Called with a fraction between 0 and 1 while the body is sent. */
  onProgress?: (fraction: number) => void;
  signal?: AbortSignal;
}

/**
 * POST one photo as multipart/form-data. XMLHttpRequest is used instead of
 * fetch because only XHR reports upload progress in every browser.
 */
export function uploadPhoto(eventId: string, file: Blob, filename: string, options: UploadOptions = {}): Promise<Photo> {
  return new Promise<Photo>((resolve, reject) => {
    if (options.signal?.aborted) {
      reject(new DOMException("Upload cancelled", "AbortError"));
      return;
    }
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl(`/api/events/${encodeURIComponent(eventId)}/photos`));
    xhr.setRequestHeader("Accept", "application/json");
    // Always sent: the server only counts it while it limits photos per guest.
    xhr.setRequestHeader("X-Guest-Key", guestKeys.get(eventId));
    xhr.withCredentials = false;
    xhr.timeout = 5 * 60 * 1000;

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) options.onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status === 201) {
        try {
          resolve(JSON.parse(xhr.responseText) as Photo);
        } catch {
          reject(networkError("The server sent an unexpected response."));
        }
        return;
      }
      reject(errorFromResponse(xhr.status, xhr.responseText, xhr.getResponseHeader("Retry-After")));
    };
    // A connection dropped mid-upload (also what browsers report when the server
    // rejects an oversized body early and closes the connection).
    xhr.onerror = () => reject(networkError("The upload was interrupted. Check your connection and try again."));
    xhr.ontimeout = () => reject(networkError("The upload took too long. Please try again."));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    options.signal?.addEventListener("abort", () => xhr.abort(), { once: true });

    const form = new FormData();
    form.append("file", file, filename);
    const name = options.uploaderName?.trim();
    const caption = options.caption?.trim();
    if (name) form.append("uploader_name", name);
    if (caption) form.append("caption", caption);
    xhr.send(form);
  });
}
