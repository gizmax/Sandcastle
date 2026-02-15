import { useCallback, useState } from "react";
import { cn } from "@/lib/utils";

interface JsonEditorProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  rows?: number;
}

export function JsonEditor({ value, onChange, placeholder, className, rows = 8 }: JsonEditorProps) {
  const [error, setError] = useState<string | null>(null);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const val = e.target.value;
      onChange(val);
      if (val.trim() === "") {
        setError(null);
        return;
      }
      try {
        JSON.parse(val);
        setError(null);
      } catch (err) {
        setError((err as Error).message);
      }
    },
    [onChange]
  );

  return (
    <div className={cn("space-y-1", className)}>
      <textarea
        value={value}
        onChange={handleChange}
        placeholder={placeholder || '{\n  "key": "value"\n}'}
        rows={rows}
        spellCheck={false}
        className={cn(
          "w-full rounded-lg border bg-surface px-3 py-2 font-mono text-sm",
          "placeholder:text-muted-foreground/50",
          "focus:outline-none focus:ring-2 focus:ring-ring/30",
          "transition-all duration-200 resize-y",
          error ? "border-error" : "border-border"
        )}
      />
      {error && <p className="text-xs text-error">{error}</p>}
    </div>
  );
}
