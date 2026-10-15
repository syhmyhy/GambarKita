import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, type ButtonSize, type ButtonVariant } from "./ui/Button";
import { useToast } from "./ui/Toast";

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback for older browsers / non-secure contexts (e.g. http:// on a LAN IP).
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      area.remove();
    }
  }
}

export function CopyButton({
  text,
  label = "Copy",
  variant = "secondary",
  size = "sm",
}: {
  text: string;
  label?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 2000);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <>
    <Button
      variant={variant}
      size={size}
      icon={copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
      onClick={async () => {
        if (await copyText(text)) setCopied(true);
        else toast.error("Couldn't copy", "Select the text and copy it manually.");
      }}
    >
      {copied ? "Copied" : label}
    </Button>
    <span className="sr-only" role="status">
      {copied ? "Copied to clipboard" : ""}
    </span>
    </>
  );
}
