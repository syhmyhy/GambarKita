import { QueueTile } from "./QueueTile";
import type { UploadItem } from "./uploadQueue";

interface UploadQueueGridProps {
  items: readonly UploadItem[];
  onRetry: (id: string) => void;
  onRemove: (id: string) => void;
}

export function UploadQueueGrid({ items, onRetry, onRemove }: UploadQueueGridProps) {
  return (
    // Failed photos span two columns to leave room for the explanation; "dense" backfills the gaps.
    <ul aria-label="Your photos" className="grid grid-flow-row-dense grid-cols-2 gap-2 sm:grid-cols-3 sm:gap-3">
      {items.map((item, index) => (
        <li key={item.id} className={item.status === "error" ? "col-span-2" : undefined}>
          <QueueTile item={item} number={index + 1} onRetry={onRetry} onRemove={onRemove} />
        </li>
      ))}
    </ul>
  );
}
