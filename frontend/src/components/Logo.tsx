import { cn } from "./ui/cn";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={cn("size-8", className)} aria-hidden="true">
      <rect width="64" height="64" rx="16" className="fill-brand-600 dark:fill-brand-500" />
      <path
        d="M21 22h4.2l2.6-3.6h8.4l2.6 3.6H43a5 5 0 0 1 5 5v14a5 5 0 0 1-5 5H21a5 5 0 0 1-5-5V27a5 5 0 0 1 5-5z"
        fill="#fff"
      />
      <path
        d="M32 40.5l-5.4-5.2a3.4 3.4 0 0 1 4.8-4.8l.6.6.6-.6a3.4 3.4 0 0 1 4.8 4.8z"
        className="fill-brand-600 dark:fill-brand-500"
      />
    </svg>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5 text-lg font-extrabold tracking-tight", className)}>
      <LogoMark />
      <span>
        Gambar<span className="text-brand-600 dark:text-brand-400">Kita</span>
      </span>
    </span>
  );
}
