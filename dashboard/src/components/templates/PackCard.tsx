import { ArrowRight, Download } from "lucide-react";
import { cn } from "@/lib/utils";
import { TOOL_ICON_MAP } from "@/components/integrations/toolIcons";
import type { TemplatePack } from "@/lib/templatePacks";

interface PackCardProps {
  pack: TemplatePack;
  count: number;
  onClick: () => void;
  onInstall?: () => void;
}

export function PackCard({ pack, count, onClick, onInstall }: PackCardProps) {
  const Icon = pack.icon;

  // Show first 3 template names, then "+N more"
  const previewNames = pack.templates.slice(0, 3).map((n) => n.replace(/_/g, " "));
  const extraCount = pack.templates.length - 3;

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group rounded-xl border p-5 text-left transition-all duration-200",
        "border-border bg-surface hover:shadow-md",
        "hover:scale-[1.02]",
        `hover:${pack.color.border}`
      )}
    >
      <div className="flex items-start justify-between mb-3">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg", pack.color.bg)}>
          <Icon className={cn("h-5 w-5", pack.color.text)} />
        </div>
        <ArrowRight className="h-4 w-4 text-muted opacity-0 group-hover:opacity-100 transition-opacity mt-1" />
      </div>
      <h3 className="text-sm font-semibold text-foreground mb-1">{pack.name}</h3>
      <p className="text-xs text-muted leading-relaxed mb-2 line-clamp-2">{pack.description}</p>

      {/* Template name preview */}
      <p className="text-[11px] text-muted-foreground mb-3 line-clamp-1">
        {previewNames.join(", ")}
        {extraCount > 0 && ` +${extraCount} more`}
      </p>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {pack.tools.slice(0, 4).map((tool) => {
            const ToolIcon = TOOL_ICON_MAP[tool];
            if (!ToolIcon) return null;
            return (
              <div
                key={tool}
                className="flex h-6 w-6 items-center justify-center rounded bg-border/40"
                title={tool}
              >
                <ToolIcon className="h-3.5 w-3.5 text-muted-foreground" />
              </div>
            );
          })}
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">
            {count} {count === 1 ? "template" : "templates"}
          </span>
          {onInstall && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onInstall();
              }}
              className={cn(
                "flex items-center gap-1 rounded-md border border-border px-2 py-1",
                "text-[11px] font-medium text-muted-foreground",
                "hover:text-foreground hover:border-accent/40 hover:bg-accent/5",
                "transition-all duration-150"
              )}
            >
              <Download className="h-3 w-3" />
              Install
            </button>
          )}
        </div>
      </div>
    </button>
  );
}
