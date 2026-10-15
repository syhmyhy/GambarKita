import { Camera, Images } from "lucide-react";
import { type ChangeEvent, useRef } from "react";

import { Button } from "../ui/Button";

/**
 * The two big actions. Each opens a hidden file input (camera, or the photo library).
 * Phone pickers can't be told "at most 6", so with ``maxFiles`` the gallery button says how
 * many fit, and with one left the picker only allows one.
 */
export function PhotoPickers({
  onFiles,
  disabled = false,
  maxFiles = null,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  /** How many photos the guest can share now; null when there is no limit. */
  maxFiles?: number | null;
}) {
  const cameraInput = useRef<HTMLInputElement>(null);
  const galleryInput = useRef<HTMLInputElement>(null);
  const galleryLabel =
    maxFiles === null || maxFiles <= 0
      ? "Choose from gallery"
      : maxFiles === 1
        ? "Choose 1 photo"
        : `Choose up to ${maxFiles} photos`;

  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.currentTarget;
    const files = Array.from(input.files ?? []);
    // Reset so picking the same photo again still fires "change".
    input.value = "";
    if (files.length > 0) onFiles(files);
  };

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Button
        size="lg"
        className="w-full"
        disabled={disabled}
        icon={<Camera className="size-5" aria-hidden="true" />}
        onClick={() => cameraInput.current?.click()}
      >
        Take a photo
      </Button>
      <Button
        size="lg"
        className="w-full"
        disabled={disabled}
        icon={<Images className="size-5" aria-hidden="true" />}
        onClick={() => galleryInput.current?.click()}
      >
        {galleryLabel}
      </Button>
      <input
        ref={cameraInput}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        aria-label="Take a photo with your camera"
        onChange={handleChange}
      />
      <input
        ref={galleryInput}
        type="file"
        accept="image/*"
        multiple={maxFiles !== 1}
        hidden
        aria-label="Choose photos from your gallery"
        onChange={handleChange}
      />
    </div>
  );
}
