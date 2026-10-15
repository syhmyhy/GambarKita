import { Camera, Images } from "lucide-react";
import { type ChangeEvent, useRef } from "react";

import { Button } from "../ui/Button";

/** The two big actions. Each opens a hidden file input (camera, or the photo library). */
export function PhotoPickers({ onFiles, disabled = false }: { onFiles: (files: File[]) => void; disabled?: boolean }) {
  const cameraInput = useRef<HTMLInputElement>(null);
  const galleryInput = useRef<HTMLInputElement>(null);

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
        Choose from gallery
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
        multiple
        hidden
        aria-label="Choose photos from your gallery"
        onChange={handleChange}
      />
    </div>
  );
}
