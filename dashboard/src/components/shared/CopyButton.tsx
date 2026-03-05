import { useState, useCallback } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";

interface CopyButtonProps {
  value: string;
  label?: string;
  className?: string;
}

export function CopyButton({ value, label, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation();
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [value]);

  const Icon = copied ? Check : Copy;

  return (
    <button
      onClick={(e) => void handleCopy(e)}
      aria-label={`Copy ${label ?? "value"} to clipboard`}
      className={cn(
        "p-1 rounded hover:bg-border/40 transition-colors",
        copied ? "text-success" : "text-muted-foreground",
        className,
      )}
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}
