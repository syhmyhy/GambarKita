import type { ButtonHTMLAttributes, ComponentProps, ReactNode } from "react";
import { Link } from "react-router";

import { cn } from "./cn";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "light";
export type ButtonSize = "sm" | "md" | "lg";

const base =
  "inline-flex select-none items-center justify-center gap-2 rounded-full font-semibold transition " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 " +
  "disabled:cursor-not-allowed disabled:opacity-50 active:scale-[0.98]";

const variants: Record<ButtonVariant, string> = {
  primary: "bg-brand-600 text-white shadow-sm shadow-brand-900/20 hover:bg-brand-700 dark:bg-brand-500 dark:hover:bg-brand-400 dark:text-stone-950",
  secondary:
    "bg-white text-stone-800 ring-1 ring-stone-200 hover:bg-stone-100 dark:bg-stone-900 dark:text-stone-100 dark:ring-stone-700 dark:hover:bg-stone-800",
  ghost: "text-stone-700 hover:bg-stone-900/5 dark:text-stone-300 dark:hover:bg-white/10",
  danger: "bg-red-600 text-white hover:bg-red-700 dark:bg-red-500 dark:text-stone-950 dark:hover:bg-red-400",
  // For use on photos / the dark wall.
  light: "bg-white/10 text-white ring-1 ring-white/20 backdrop-blur hover:bg-white/20",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-9 px-3.5 text-sm",
  md: "h-11 px-5 text-sm",
  lg: "h-14 px-7 text-base",
};

export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "md", extra?: string): string {
  return cn(base, variants[variant], sizes[size], extra);
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  icon?: ReactNode;
}

export function Button({ variant, size, loading, icon, className, children, disabled, type, ...rest }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={buttonClasses(variant, size, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Spinner className="size-4" /> : icon}
      {children}
    </button>
  );
}

interface ButtonLinkProps extends ComponentProps<typeof Link> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
}

export function ButtonLink({ variant, size, icon, className, children, ...rest }: ButtonLinkProps) {
  return (
    <Link className={buttonClasses(variant, size, typeof className === "string" ? className : undefined)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}

/** Square icon-only button; ``label`` is required for screen readers. */
export function IconButton({
  label,
  variant = "ghost",
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; variant?: ButtonVariant }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(base, variants[variant], "size-11 shrink-0 p-0", className)}
      {...rest}
    >
      {children}
    </button>
  );
}
