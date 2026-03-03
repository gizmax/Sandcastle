import { useState, useRef } from "react";
import { Loader2, Upload } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

interface FilePathInputProps {
  value: string;
  onChange: (v: string) => void;
  accept?: string;
}

export function FilePathInput({ value, onChange, accept }: FilePathInputProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileName = value ? value.split("/").pop() : "";

  async function handleFileSelect(file: File) {
    setError(null);
    setUploading(true);
    try {
      const res = await api.uploadFile(file);
      if (res.error) {
        setError(res.error.message);
      } else if (res.data) {
        onChange(res.data.path);
      }
    } catch {
      setError("Upload failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <div className="flex gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => {
            setError(null);
            onChange(e.target.value);
          }}
          placeholder="Select file or paste path..."
          className={cn(
            "h-9 flex-1 rounded-lg border border-border bg-background px-3 text-sm",
            "focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-ring/30"
          )}
        />
        <input
          ref={fileRef}
          type="file"
          accept={accept}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleFileSelect(file);
            // Reset so re-selecting the same file triggers onChange
            if (fileRef.current) fileRef.current.value = "";
          }}
        />
        <button
          type="button"
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
          className={cn(
            "flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-sm",
            "text-muted hover:text-foreground hover:border-accent/50 transition-colors",
            uploading && "opacity-50 cursor-not-allowed"
          )}
        >
          {uploading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Upload className="h-3.5 w-3.5" />
          )}
          {uploading ? "Uploading..." : fileName ? "Change" : "Browse"}
        </button>
      </div>
      {error && (
        <p className="mt-1 text-xs text-error">{error}</p>
      )}
    </div>
  );
}
