import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_EDGE, PhotoPrepError, fitWithin, isHeic, jpegName, needsConversion, preparePhoto } from "../image";

const LIMITS = { maxBytes: 1000, allowedTypes: ["image/jpeg", "image/png", "image/webp"] } as const;

function fileOf(size: number, name: string, type: string): File {
  return new File([new Uint8Array(size)], name, { type });
}

async function rejection(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the promise to reject");
}

describe("isHeic", () => {
  it.each([
    ["IMG_0001.HEIC", "image/heic"],
    ["IMG_0001.heif", "image/heif"],
    ["photo", "image/HEIC"],
    ["IMG_0001.heic", ""], // some browsers leave the type empty
    ["burst.HEIF", "application/octet-stream"],
  ])("detects %s (%s)", (name, type) => {
    expect(isHeic({ name, type })).toBe(true);
  });

  it.each([
    ["photo.jpg", "image/jpeg"],
    ["heic.png", "image/png"],
    ["IMG.heic.jpg", "image/jpeg"],
    ["", ""],
  ])("does not flag %s (%s)", (name, type) => {
    expect(isHeic({ name, type })).toBe(false);
  });
});

describe("needsConversion", () => {
  it("is false for an allowed type within the size limit", () => {
    expect(needsConversion({ type: "image/jpeg", size: 1000 }, LIMITS)).toBe(false);
    expect(needsConversion({ type: "image/webp", size: 1 }, LIMITS)).toBe(false);
  });

  it("is true for a type the server does not accept", () => {
    expect(needsConversion({ type: "image/heic", size: 10 }, LIMITS)).toBe(true);
    expect(needsConversion({ type: "", size: 10 }, LIMITS)).toBe(true);
  });

  it("is true over the size limit", () => {
    expect(needsConversion({ type: "image/jpeg", size: 1001 }, LIMITS)).toBe(true);
  });
});

describe("fitWithin", () => {
  it("scales a landscape image so its width is the max edge", () => {
    expect(fitWithin(8000, 6000)).toEqual({ width: MAX_EDGE, height: 3072 });
  });

  it("scales a portrait image so its height is the max edge", () => {
    expect(fitWithin(3000, 9000, 4096)).toEqual({ width: 1365, height: 4096 });
  });

  it("leaves small images unchanged (never upscales)", () => {
    expect(fitWithin(640, 480)).toEqual({ width: 640, height: 480 });
    expect(fitWithin(4096, 4096)).toEqual({ width: 4096, height: 4096 });
  });

  it("honours a custom max edge", () => {
    expect(fitWithin(1000, 500, 100)).toEqual({ width: 100, height: 50 });
  });

  it("never returns a zero-sized edge", () => {
    expect(fitWithin(100_000, 1, 1000)).toEqual({ width: 1000, height: 1 });
  });
});

describe("jpegName", () => {
  it.each([
    ["IMG_0001.HEIC", "IMG_0001.jpg"],
    ["holiday.photo.png", "holiday.photo.jpg"],
    ["no-extension", "no-extension.jpg"],
    [".heic", "photo.jpg"],
    ["", "photo.jpg"],
    ["   .png", "photo.jpg"],
    ["  spaced name .webp", "spaced name.jpg"],
  ])("%j -> %j", (input, expected) => {
    expect(jpegName(input)).toBe(expected);
  });
});

describe("preparePhoto", () => {
  const createImageBitmap = vi.fn<(image: ImageBitmapSource, options?: ImageBitmapOptions) => Promise<ImageBitmap>>();

  beforeEach(() => {
    createImageBitmap.mockReset();
    vi.stubGlobal("createImageBitmap", createImageBitmap);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes an allowed, small file through untouched", async () => {
    const file = fileOf(500, "IMG_0001.jpg", "image/jpeg");
    await expect(preparePhoto(file, LIMITS)).resolves.toEqual({ blob: file, filename: "IMG_0001.jpg", converted: false });
    expect(createImageBitmap).not.toHaveBeenCalled();
  });

  it("names an unnamed passthrough file", async () => {
    const file = fileOf(10, "", "image/png");
    const prepared = await preparePhoto(file, LIMITS);
    expect(prepared.filename).toBe("photo.jpg");
    expect(prepared.blob).toBe(file);
  });

  it("rejects an empty file", async () => {
    const error = await rejection(preparePhoto(fileOf(0, "empty.jpg", "image/jpeg"), LIMITS));
    expect(error).toBeInstanceOf(PhotoPrepError);
    expect((error as PhotoPrepError).name).toBe("PhotoPrepError");
    expect((error as PhotoPrepError).message).toBe("This file is empty.");
  });

  it("refuses to decode an enormous file", async () => {
    const file = fileOf(10, "huge.jpg", "image/jpeg");
    Object.defineProperty(file, "size", { value: 61 * 1024 * 1024 });
    await expect(preparePhoto(file, LIMITS)).rejects.toThrow("This file is too large to upload.");
    expect(createImageBitmap).not.toHaveBeenCalled();
  });

  it("explains HEIC decode failures", async () => {
    createImageBitmap.mockRejectedValue(new DOMException("The source image could not be decoded.", "InvalidStateError"));
    const error = await rejection(preparePhoto(fileOf(10, "IMG_0001.HEIC", "image/heic"), LIMITS));
    expect(error).toBeInstanceOf(PhotoPrepError);
    expect((error as Error).message).toMatch(/can't read HEIC photos/);
    expect((error as Error).message).toMatch(/Most Compatible/);
  });

  it("recognises HEIC by extension when the type is missing", async () => {
    createImageBitmap.mockRejectedValue(new Error("decode failed"));
    await expect(preparePhoto(fileOf(10, "IMG_0001.heic", ""), LIMITS)).rejects.toThrow(/HEIC/);
  });

  it("gives a generic message for other undecodable files", async () => {
    createImageBitmap.mockRejectedValue(new Error("decode failed"));
    const error = await rejection(preparePhoto(fileOf(10, "notes.pdf", "application/pdf"), LIMITS));
    expect(error).toBeInstanceOf(PhotoPrepError);
    expect((error as Error).message).toBe("This file isn't a photo we can use. Please choose a JPEG, PNG or WebP image.");
  });

  describe("conversion", () => {
    interface FakeContext {
      fillStyle: string;
      imageSmoothingQuality: string;
      fillRect: ReturnType<typeof vi.fn>;
      drawImage: ReturnType<typeof vi.fn>;
    }
    let context: FakeContext | null;
    let blobSizes: number[];
    let renders: { width: number; height: number; type: string | undefined; quality: number | undefined }[];
    let bitmap: { width: number; height: number; close: ReturnType<typeof vi.fn> };

    beforeEach(() => {
      context = { fillStyle: "", imageSmoothingQuality: "low", fillRect: vi.fn(), drawImage: vi.fn() };
      blobSizes = [];
      renders = [];
      bitmap = { width: 8000, height: 6000, close: vi.fn() };
      createImageBitmap.mockResolvedValue(bitmap as unknown as ImageBitmap);

      vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation((() => context) as never);
      vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(function (
        this: HTMLCanvasElement,
        callback: BlobCallback,
        type?: string,
        quality?: number,
      ) {
        renders.push({ width: this.width, height: this.height, type, quality });
        const size = blobSizes.shift() ?? 10;
        callback(size < 0 ? null : new Blob([new Uint8Array(size)], { type: "image/jpeg" }));
      });
    });

    it("converts HEIC to a JPEG that fits the limits", async () => {
      const file = fileOf(5000, "IMG_0001.HEIC", "image/heic");
      const prepared = await preparePhoto(file, LIMITS);

      expect(prepared.converted).toBe(true);
      expect(prepared.filename).toBe("IMG_0001.jpg");
      expect(prepared.blob.type).toBe("image/jpeg");
      expect(prepared.blob.size).toBeLessThanOrEqual(LIMITS.maxBytes);
      expect(createImageBitmap).toHaveBeenCalledWith(file, { imageOrientation: "from-image" });
      expect(renders).toEqual([{ width: 4096, height: 3072, type: "image/jpeg", quality: 0.9 }]);
      expect(context?.fillStyle).toBe("#ffffff");
      expect(context?.fillRect).toHaveBeenCalledWith(0, 0, 4096, 3072);
      expect(context?.drawImage).toHaveBeenCalledWith(bitmap, 0, 0, 4096, 3072);
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it("lowers the quality, then the size, until the photo fits", async () => {
      blobSizes = [5000, 4000, 3000, 2000, 1500, 900];
      const prepared = await preparePhoto(fileOf(2000, "big.jpg", "image/jpeg"), LIMITS);

      expect(prepared.blob.size).toBe(900);
      expect(prepared.filename).toBe("big.jpg");
      expect(renders.map(({ width, height, quality }) => [width, height, quality])).toEqual([
        [4096, 3072, 0.9],
        [4096, 3072, 0.82],
        [4096, 3072, 0.74],
        [4096, 3072, 0.66],
        [3072, 2304, 0.9],
        [3072, 2304, 0.82],
      ]);
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it("gives up after three rounds of shrinking", async () => {
      blobSizes = Array.from({ length: 20 }, () => 5000);
      await expect(preparePhoto(fileOf(2000, "big.jpg", "image/jpeg"), LIMITS)).rejects.toThrow(
        "This photo is too large to upload, even after shrinking it.",
      );
      expect(renders).toHaveLength(12);
      expect(renders.at(-1)).toMatchObject({ width: 2304, height: 1728, quality: 0.66 });
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it("fails cleanly when the browser has no 2D canvas", async () => {
      context = null;
      const error = await rejection(preparePhoto(fileOf(5000, "a.heic", "image/heic"), LIMITS));
      expect(error).toBeInstanceOf(PhotoPrepError);
      expect((error as Error).message).toBe("Your browser couldn't process this photo.");
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it("fails cleanly when the canvas cannot encode a JPEG", async () => {
      blobSizes = [-1];
      await expect(preparePhoto(fileOf(5000, "a.heic", "image/heic"), LIMITS)).rejects.toThrow(
        "Your browser couldn't process this photo.",
      );
      expect(bitmap.close).toHaveBeenCalledTimes(1);
    });

    it("does not upscale small photos while converting", async () => {
      bitmap.width = 800;
      bitmap.height = 600;
      await preparePhoto(fileOf(5000, "small.heic", "image/heic"), LIMITS);
      expect(renders[0]).toMatchObject({ width: 800, height: 600 });
    });
  });
});
