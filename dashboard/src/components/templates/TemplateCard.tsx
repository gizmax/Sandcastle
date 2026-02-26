import { ArrowRight, Globe } from "lucide-react";
import { cn } from "@/lib/utils";
import { getPackById } from "@/lib/templatePacks";

interface Template {
  name: string;
  description: string;
  tags: string[];
  step_count: number;
  category?: string | null;
  source?: "community";
  author?: string;
}

const TAG_COLORS = [
  "bg-accent/15 text-accent",
  "bg-running/15 text-running",
  "bg-success/15 text-success",
  "bg-queued/15 text-queued",
  "bg-error/15 text-error",
  "bg-warning/15 text-warning",
];

function tagColor(tag: string): string {
  let hash = 0;
  for (let i = 0; i < tag.length; i++) {
    hash = tag.charCodeAt(i) + ((hash << 5) - hash);
  }
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}

interface TemplateCardProps {
  template: Template;
  isSelected: boolean;
  onClick: () => void;
}

export function TemplateCard({ template, isSelected, onClick }: TemplateCardProps) {
  const pack = template.category ? getPackById(template.category) : null;
  const accentBorder = pack ? pack.color.border : "border-border";

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group rounded-xl border p-4 text-left transition-all duration-200",
        isSelected
          ? "border-accent ring-2 ring-accent/20 bg-accent/5"
          : `border-border bg-surface hover:shadow-md hover:bg-surface hover:${accentBorder}`
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          {pack && (
            <div className={cn("h-1.5 w-1.5 rounded-full", pack.color.text.replace("text-", "bg-"))} />
          )}
          <span className="text-sm font-semibold text-foreground">
            {template.name.replace(/_/g, " ")}
          </span>
          {template.source === "community" && (
            <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 border border-amber-500/30 px-1.5 py-0.5 text-[10px] font-semibold text-amber-500">
              <Globe className="h-2.5 w-2.5" />
              Community
            </span>
          )}
        </div>
        <ArrowRight className="h-4 w-4 text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>
      <p className="text-xs text-muted leading-relaxed mb-3 line-clamp-2">
        {template.description}
      </p>
      <div className="flex items-center justify-between">
        <div className="flex flex-wrap gap-1.5">
          {template.tags.map((tag) => (
            <span
              key={tag}
              className={cn(
                "rounded-full px-2 py-0.5 text-[10px] font-medium",
                tagColor(tag)
              )}
            >
              {tag}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-2">
          {template.source === "community" && template.author && (
            <span className="text-[10px] text-muted-foreground font-mono">
              @{template.author}
            </span>
          )}
          <span className="text-[11px] text-muted-foreground font-mono">
            {template.step_count} {template.step_count === 1 ? "step" : "steps"}
          </span>
        </div>
      </div>
    </button>
  );
}
