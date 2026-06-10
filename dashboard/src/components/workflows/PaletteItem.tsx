/**
 * PaletteItem — a single step button in the Workflow Builder left palette.
 *
 * Wraps the add-step button in a rich `HoverCard` so users understand what a
 * step type does before adding it: title = label, body = summary + "Use when…"
 * + an example, footer = cost note. Metadata comes from `getStepMeta` (and
 * `getAgentTemplateMeta` for the 15 agent personas) in lib/builder/stepMetadata.
 *
 * The hover card opens on hover AND keyboard focus (HoverCard handles both), so
 * the palette is learnable for keyboard users too. When "Learn mode" is on the
 * parent passes `showInlineSummary`, and the one-line summary is rendered inline
 * beneath the label (always visible) for first-timers.
 */
import type { ComponentType } from "react";
import { HoverCard } from "@/components/shared/HoverCard";
import {
  getStepMeta,
  getAgentTemplateMeta,
} from "@/lib/builder/stepMetadata";
import { cn } from "@/lib/utils";

export interface PaletteItemProps {
  /** Step type discriminator (e.g. "race", "agent"). */
  type: string;
  /** Optional agent template id (for the Agents category). */
  template?: string;
  /** lucide icon component. */
  icon: ComponentType<{ className?: string }>;
  /** Display label as shown in the palette. */
  label: string;
  /** Tailwind colour class for the icon. */
  color?: string;
  /** When true, show the one-line summary inline beneath the label. */
  showInlineSummary?: boolean;
  /** Add-step handler. */
  onAdd: () => void;
}

/**
 * Resolves the human summary / when-to-use / example / cost note for an item,
 * preferring the agent-template copy when this is one of the 15 personas.
 */
function resolveCopy(type: string, template?: string) {
  if (type === "agent" && template) {
    const meta = getAgentTemplateMeta(template);
    return {
      summary: meta.summary,
      whenToUse: meta.whenToUse,
      example: undefined as string | undefined,
      costNote: getStepMeta("agent").costNote,
    };
  }
  const meta = getStepMeta(type);
  return {
    summary: meta.summary,
    whenToUse: meta.whenToUse,
    example: meta.example,
    costNote: meta.costNote,
  };
}

export function PaletteItem({
  type,
  template,
  icon: Icon,
  label,
  color,
  showInlineSummary,
  onAdd,
}: PaletteItemProps) {
  const { summary, whenToUse, example, costNote } = resolveCopy(type, template);

  const body = [
    summary,
    `Use when: ${whenToUse}`,
    ...(example ? [`e.g. ${example}`] : []),
  ];

  return (
    <HoverCard
      title={label}
      body={body}
      footer={costNote}
      side="top"
      className="w-full"
    >
      <button
        type="button"
        onClick={onAdd}
        data-palette-item={template ? `${type}:${template}` : type}
        aria-label={`Add ${label} step. ${summary}`}
        className={cn(
          "flex w-full flex-col gap-0.5 rounded-lg border border-dashed border-border px-2 py-1.5 text-left",
          "text-[11px] font-medium text-muted hover:border-accent hover:text-accent transition-colors",
        )}
      >
        <span className="flex items-center gap-1.5">
          <Icon className={cn("h-3 w-3 shrink-0", color)} />
          <span className="truncate">{label}</span>
        </span>
        {showInlineSummary && (
          <span className="mt-0.5 line-clamp-2 text-[10px] font-normal leading-snug text-muted/80">
            {summary}
          </span>
        )}
      </button>
    </HoverCard>
  );
}
