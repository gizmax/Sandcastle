import { Copy, Download, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface YamlPreviewProps {
  open: boolean;
  yaml: string;
  onClose: () => void;
}

export function YamlPreview({ open, yaml, onClose }: YamlPreviewProps) {
  if (!open) return null;

  function handleCopy() {
    navigator.clipboard.writeText(yaml);
  }

  function handleDownload() {
    const blob = new Blob([yaml], { type: "text/yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "workflow.yaml";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l border-border bg-surface shadow-xl">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <h3 className="text-sm font-semibold text-foreground">YAML Preview</h3>
        <div className="flex items-center gap-1">
          <button
            onClick={handleCopy}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground"
            title="Copy to clipboard"
          >
            <Copy className="h-4 w-4" />
          </button>
          <button
            onClick={handleDownload}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground"
            title="Download .yaml"
          >
            <Download className="h-4 w-4" />
          </button>
          <button
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-border/50 hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      <pre
        className={cn(
          "flex-1 overflow-auto p-5 font-mono text-xs leading-relaxed text-foreground",
          "bg-background"
        )}
      >
        {yaml || "# No steps defined yet"}
      </pre>
    </div>
  );
}
