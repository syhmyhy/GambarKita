import { TextField } from "../ui/TextField";

interface GuestDetailsFieldsProps {
  name: string;
  onNameChange: (value: string) => void;
  caption: string;
  onCaptionChange: (value: string) => void;
  maxNameLength: number;
  maxCaptionLength: number;
}

/** Optional name and message, attached to the next photos the guest picks. */
export function GuestDetailsFields({
  name,
  onNameChange,
  caption,
  onCaptionChange,
  maxNameLength,
  maxCaptionLength,
}: GuestDetailsFieldsProps) {
  return (
    <div className="space-y-4">
      <TextField
        label="Your name"
        optional
        placeholder="So everyone knows who took it"
        value={name}
        onChange={onNameChange}
        maxLength={maxNameLength}
        autoComplete="name"
        autoCapitalize="words"
        enterKeyHint="next"
      />
      <TextField
        label="Message"
        optional
        placeholder="e.g. Congratulations, you two!"
        value={caption}
        onChange={onCaptionChange}
        maxLength={maxCaptionLength}
        showCount
        hint="Added to the photos you send next."
        autoComplete="off"
        autoCapitalize="sentences"
        enterKeyHint="done"
      />
    </div>
  );
}
