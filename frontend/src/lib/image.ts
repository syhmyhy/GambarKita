/**
 * Client-side photo preparation. The server validates everything again; this
 * only makes the common cases work smoothly on phones:
 *
 * - photos over the size limit are downscaled and re-compressed in the browser
 *   instead of being rejected after a long upload;
 * - formats the server does not accept (e.g. HEIC on iPhone) are converted to
 *   JPEG when the browser can decode them (Safari can).
 */

export const MAX_EDGE = 4096; // matches the server's stored-copy size
const MAX_DECODABLE_BYTES = 60 * 1024 * 1024; // don't try to decode absurdly large files on a phone
const QUALITY_STEPS = [0.9, 0.82, 0.74, 0.66];

export class PhotoPrepError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PhotoPrepError";
  }
}

export interface PrepLimits {
  maxBytes: number;
  allowedTypes: readonly string[];
}

export interface PreparedPhoto {
  blob: Blob;
  filename: string;
  /** True when the photo was converted or shrunk in the browser. */
  converted: boolean;
}

export function isHeic(file: Pick<File, "name" | "type">): boolean {
  return /image\/hei[cf]/i.test(file.type) || /\.(heic|heif)$/i.test(file.name);
}

/** Whether the file can be sent as-is, or must be converted/shrunk first. */
export function needsConversion(file: Pick<File, "type" | "size">, limits: PrepLimits): boolean {
  return !limits.allowedTypes.includes(file.type) || file.size > limits.maxBytes;
}

/** Scale (width, height) down so the longest edge is at most ``maxEdge``. */
export function fitWithin(width: number, height: number, maxEdge: number = MAX_EDGE): { width: number; height: number } {
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

export function jpegName(filename: string): string {
  const base = filename.replace(/\.[^./\\]*$/, "").trim() || "photo";
  return `${base}.jpg`;
}

export async function preparePhoto(file: File, limits: PrepLimits): Promise<PreparedPhoto> {
  if (file.size === 0) throw new PhotoPrepError("This file is empty.");
  if (!needsConversion(file, limits)) return { blob: file, filename: file.name || "photo.jpg", converted: false };
  if (file.size > MAX_DECODABLE_BYTES) throw new PhotoPrepError("This file is too large to upload.");

  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    if (isHeic(file)) {
      throw new PhotoPrepError(
        "This browser can't read HEIC photos. On iPhone, pick the photo from your library in Safari, " +
          "or set Settings › Camera › Formats to “Most Compatible”.",
      );
    }
    throw new PhotoPrepError("This file isn't a photo we can use. Please choose a JPEG, PNG or WebP image.");
  }

  try {
    let { width, height } = fitWithin(bitmap.width, bitmap.height);
    for (let attempt = 0; attempt < 3; attempt += 1) {
      for (const quality of QUALITY_STEPS) {
        const blob = await renderJpeg(bitmap, width, height, quality);
        if (blob.size <= limits.maxBytes) return { blob, filename: jpegName(file.name), converted: true };
      }
      width = Math.round(width * 0.75);
      height = Math.round(height * 0.75);
    }
  } finally {
    bitmap.close();
  }
  throw new PhotoPrepError("This photo is too large to upload, even after shrinking it.");
}

function renderJpeg(bitmap: ImageBitmap, width: number, height: number, quality: number): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new PhotoPrepError("Your browser couldn't process this photo.");
  context.fillStyle = "#ffffff"; // JPEG has no transparency
  context.fillRect(0, 0, width, height);
  context.imageSmoothingQuality = "high";
  context.drawImage(bitmap, 0, 0, width, height);
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new PhotoPrepError("Your browser couldn't process this photo."))),
      "image/jpeg",
      quality,
    );
  });
}
