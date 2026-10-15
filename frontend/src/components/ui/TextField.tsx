import { type InputHTMLAttributes, useId } from "react";

import { cn } from "./cn";

interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "onChange"> {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error?: string | null;
  /** Show a live "12/80" counter (requires maxLength). */
  showCount?: boolean;
  optional?: boolean;
}

export function TextField({
  label,
  value,
  onChange,
  hint,
  error,
  showCount,
  optional,
  maxLength,
  className,
  id,
  ...rest
}: TextFieldProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const describedBy = [hint ? `${inputId}-hint` : null, error ? `${inputId}-error` : null].filter(Boolean).join(" ");

  return (
    <div className={className}>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <label htmlFor={inputId} className="text-sm font-semibold text-stone-800 dark:text-stone-200">
          {label}
          {optional && <span className="ml-1.5 font-normal text-stone-500 dark:text-stone-400">(optional)</span>}
        </label>
        {showCount && maxLength ? (
          <span className="text-xs tabular-nums text-stone-500 dark:text-stone-400" aria-hidden="true">
            {value.length}/{maxLength}
          </span>
        ) : null}
      </div>
      <input
        id={inputId}
        value={value}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={cn(
          "h-12 w-full rounded-xl bg-white px-4 text-base text-stone-900 shadow-sm transition",
          "placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-brand-500",
          "dark:bg-stone-950 dark:text-stone-100 dark:placeholder:text-stone-500",
          error ? "ring-2 ring-red-500" : "ring-1 ring-stone-300 dark:ring-stone-700",
        )}
        {...rest}
      />
      {hint && !error && (
        <p id={`${inputId}-hint`} className="mt-1.5 text-sm text-stone-500 dark:text-stone-400">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${inputId}-error`} className="mt-1.5 text-sm font-medium text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
