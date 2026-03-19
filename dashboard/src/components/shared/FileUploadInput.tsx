import { useState, useRef, useCallback } from "react";
import { File as FileIcon, Upload, X, Loader2, Image as ImageIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

interface FileUploadInputProps {
  /** Current value - either "" or "@upload:file_id" */
  value: string;
  onChange: (v: string) => void;
  /** File accept filter (e.g. ".pdf,.csv,.xlsx") from schema's accept property */
  accept?: string;
  required?: boolean;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function isImageType(contentType: string): boolean {
  return contentType.startsWith("image/");
}

export function FileUploadInput({ value, onChange, accept, required }: FileUploadInputProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadedFile, setUploadedFile] = useState<{
    filename: string;
    size_bytes: number;
    content_type: string;
    previewUrl?: string;
  } | null>(null);
  const [dragging, setDragging] = useState(false);

  const hasUpload = value.startsWith("@upload:");

  async function handleFileUpload(file: File) {
    setError(null);
    setUploading(true);
    try {
      const res = await api.upload(file);
      if (res.error) {
        setError(res.error.message);
      } else if (res.data) {
        onChange(`@upload:${res.data.file_id}`);
        const previewUrl = isImageType(res.data.content_type)
          ? URL.createObjectURL(file)
          : undefined;
        setUploadedFile({
          filename: res.data.filename,
          size_bytes: res.data.size_bytes,
          content_type: res.data.content_type,
          previewUrl,
        });
      }
    } catch {
      setError("Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function handleRemove() {
    if (uploadedFile?.previewUrl) {
      URL.revokeObjectURL(uploadedFile.previewUrl);
    }
    setUploadedFile(null);
    setError(null);
    onChange("");
    if (fileRef.current) fileRef.current.value = "";
  }

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) handleFileUpload(file);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const acceptText = accept
    ? `Accepted: ${accept}`
    : "Any file type supported";

  return (
    <div>
      {hasUpload && uploadedFile ? (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-3">
          {uploadedFile.previewUrl ? (
            <img
              src={uploadedFile.previewUrl}
              alt={uploadedFile.filename}
              className="h-10 w-10 rounded object-cover shrink-0"
            />
          ) : isImageType(uploadedFile.content_type) ? (
            <ImageIcon className="h-8 w-8 shrink-0 text-muted" />
          ) : (
            <FileIcon className="h-8 w-8 shrink-0 text-muted" />
          )}
          <div className="flex-1 min-w-0">
            <p className="truncate text-sm text-foreground">{uploadedFile.filename}</p>
            <p className="text-xs text-muted">{formatBytes(uploadedFile.size_bytes)}</p>
          </div>
          <button
            type="button"
            onClick={handleRemove}
            className="shrink-0 rounded p-1 text-muted hover:text-foreground transition-colors"
            aria-label="Remove file"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <div
          role="button"
          tabIndex={0}
          aria-required={required}
          onClick={() => !uploading && fileRef.current?.click()}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !uploading) {
              e.preventDefault();
              fileRef.current?.click();
            }
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={cn(
            "border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors",
            dragging
              ? "border-accent/70 bg-accent/5"
              : "border-border hover:border-accent/50",
            uploading && "pointer-events-none opacity-60"
          )}
        >
          {uploading ? (
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="h-8 w-8 text-accent animate-spin" />
              <p className="text-sm text-muted">Uploading...</p>
            </div>
          ) : (
            <>
              <Upload className="h-8 w-8 mx-auto text-muted mb-2" />
              <p className="text-sm text-muted">Drop a file here or click to browse</p>
              <p className="text-xs text-muted mt-1">{acceptText}</p>
            </>
          )}
        </div>
      )}

      <input
        ref={fileRef}
        type="file"
        accept={accept}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFileUpload(file);
          if (fileRef.current) fileRef.current.value = "";
        }}
      />

      {error && <p className="mt-1 text-xs text-error">{error}</p>}
    </div>
  );
}
