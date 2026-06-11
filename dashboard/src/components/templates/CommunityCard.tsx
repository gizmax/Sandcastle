import { Download, Eye, ExternalLink, User, GitFork, Check } from "lucide-react";
import { cn, isSafeUrl } from "@/lib/utils";

// Category label + color mappings
const CATEGORY_META: Record<string, { label: string; color: string }> = {
  sales_crm: { label: "Sales & CRM", color: "bg-amber-500" },
  marketing: { label: "Marketing", color: "bg-violet-500" },
  support: { label: "Support", color: "bg-sky-500" },
  engineering: { label: "Engineering", color: "bg-emerald-500" },
  hr_legal: { label: "HR & Legal", color: "bg-rose-500" },
  general_ai: { label: "General AI", color: "bg-indigo-500" },
};

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

export interface CommunityTemplate {
  slug: string;
  name: string;
  description: string;
  author: string;
  author_url?: string;
  category: string;
  tags?: string[];
  step_count: number;
  models_used?: string[];
  tools_used?: string[];
  download_url: string;
  created_at: string;
  estimated_cost_per_run?: number;
  avg_execution_time?: string;
  downloads?: number;
  remix_count?: number;
  forked_from?: string | null;
  source?: string;
}

interface CommunityCardProps {
  template: CommunityTemplate;
  installed?: boolean;
  onInstall: () => void;
  onPreview: () => void;
}

export function CommunityCard({ template, installed, onInstall, onPreview }: CommunityCardProps) {
  const cat = CATEGORY_META[template.category] || {
    label: template.category,
    color: "bg-muted",
  };

  return (
    <div
      className={cn(
        "group rounded-xl border border-border bg-surface p-4 text-left",
        "transition-settle hover:shadow-md hover:border-accent/30"
      )}
    >
      {/* Top row: category dot + source badge */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <div className={cn("h-1.5 w-1.5 rounded-full", cat.color)} />
          <span className="text-[10px] font-medium text-muted uppercase tracking-wide">
            {cat.label}
          </span>
        </div>
        <span className="rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-semibold text-accent uppercase tracking-wide">
          Community
        </span>
      </div>

      {/* Name + installed badge */}
      <div className="flex items-center gap-2 mb-1">
        <h3 className="text-sm font-semibold text-foreground line-clamp-1">
          {template.name}
        </h3>
        {installed && (
          <span className="inline-flex items-center gap-1 rounded-full bg-success/15 border border-success/30 px-2 py-0.5 text-[10px] font-semibold text-success shrink-0">
            <Check className="h-3 w-3" />
            Installed
          </span>
        )}
      </div>

      {/* Description */}
      <p className="text-xs text-muted leading-relaxed mb-3 line-clamp-2">
        {template.description}
      </p>

      {/* Author */}
      <div className="flex items-center gap-1.5 mb-1">
        <User className="h-3 w-3 text-muted-foreground" />
        {template.author_url && isSafeUrl(template.author_url) ? (
          <a
            href={template.author_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="text-[11px] text-accent hover:text-accent-hover transition-colors flex items-center gap-0.5"
          >
            @{template.author}
            <ExternalLink className="h-2.5 w-2.5" />
          </a>
        ) : (
          <span className="text-[11px] text-muted-foreground">@{template.author}</span>
        )}
      </div>

      {/* Remixed from */}
      {template.forked_from && (
        <p className="text-[10px] text-accent/70 mb-1 ml-4.5 flex items-center gap-1">
          <GitFork className="h-2.5 w-2.5" />
          Remixed from{" "}
          <span className="font-medium text-accent hover:text-accent-hover cursor-pointer">
            {template.forked_from}
          </span>
        </p>
      )}

      <div className="mb-3" />

      {/* Tags */}
      <div className="flex flex-wrap gap-1 mb-3">
        {(template.tags || []).slice(0, 4).map((tag) => (
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
        {(template.tags || []).length > 4 && (
          <span className="rounded-full bg-border/40 px-2 py-0.5 text-[10px] text-muted">
            +{(template.tags || []).length - 4}
          </span>
        )}
      </div>

      {/* Models/tools pills + step count */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex flex-wrap gap-1">
          {(template.models_used || []).map((m) => (
            <span
              key={m}
              className="rounded-md bg-border/40 px-1.5 py-0.5 text-[10px] text-muted-foreground font-mono"
            >
              {m}
            </span>
          ))}
          {(template.tools_used || []).map((t) => (
            <span
              key={t}
              className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent font-mono"
            >
              {t}
            </span>
          ))}
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-2">
          {template.downloads != null && template.downloads > 0 && (
            <span className="flex items-center gap-0.5 text-[11px] text-muted-foreground font-data">
              <Download className="h-2.5 w-2.5" />
              {template.downloads}
            </span>
          )}
          {template.remix_count != null && template.remix_count > 0 && (
            <span className="flex items-center gap-0.5 text-[11px] text-muted-foreground font-data">
              <GitFork className="h-2.5 w-2.5" />
              {template.remix_count}
            </span>
          )}
          <span className="text-[11px] text-muted-foreground font-data">
            {template.step_count} {template.step_count === 1 ? "step" : "steps"}
          </span>
        </div>
      </div>

      {/* Cost badge + Action buttons */}
      <div className="flex items-center gap-2 pt-2 border-t border-border/60">
        {template.estimated_cost_per_run != null && (
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-mono font-medium shrink-0",
              template.estimated_cost_per_run < 0.01
                ? "bg-success/15 text-success"
                : template.estimated_cost_per_run > 0.05
                  ? "bg-error/15 text-error"
                  : "bg-accent/15 text-accent"
            )}
          >
            ~${template.estimated_cost_per_run.toFixed(3)}
          </span>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onInstall();
          }}
          className={cn(
            "flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5",
            "text-xs font-medium transition-settle shadow-sm",
            installed
              ? "border border-success/30 text-success hover:bg-success/10 bg-transparent"
              : "bg-accent text-accent-foreground hover:bg-accent-hover"
          )}
        >
          {installed ? (
            <>
              <Check className="h-3 w-3" />
              Installed
            </>
          ) : (
            <>
              <Download className="h-3 w-3" />
              Install
            </>
          )}
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onPreview();
          }}
          className={cn(
            "flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-1.5",
            "text-xs font-medium text-muted-foreground",
            "hover:text-foreground hover:bg-border/40 transition-colors"
          )}
        >
          <Eye className="h-3 w-3" />
          Preview
        </button>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onPreview();
          }}
          className={cn(
            "flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-1.5",
            "text-xs font-medium text-muted-foreground",
            "hover:text-foreground hover:bg-border/40 transition-colors"
          )}
        >
          <GitFork className="h-3 w-3" />
          Remix
        </button>
      </div>
    </div>
  );
}
