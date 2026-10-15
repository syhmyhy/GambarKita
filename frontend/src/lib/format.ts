export function formatBytes(bytes: number): string {
  // Promote to the next unit when rounding would print "1024 KB".
  if (bytes >= 1024 * 1024 - 51) return `${trimZero(bytes / (1024 * 1024))} MB`;
  if (bytes >= 1024) return `${trimZero(bytes / 1024)} KB`;
  return bytes === 1 ? "1 byte" : `${bytes} bytes`;
}

function trimZero(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "just now", "5 minutes ago", "yesterday"... */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = (new Date(iso).getTime() - now) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (Math.abs(seconds) < 45) return "just now";
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return relative.format(Math.round(seconds / 60), "minute");
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count.toLocaleString()} ${count === 1 ? singular : plural}`;
}
