import { cn } from "@/lib/utils";
import type { ProviderCostsData, ProviderSavingsData } from "./bentoTypes";

const PROVIDER_COLORS: Record<string, string> = {
  claude: "bg-accent",
  anthropic: "bg-accent",
  openai: "bg-running",
  mistral: "bg-success",
  minimax: "bg-warning",
  google: "bg-error",
  ollama: "bg-muted-foreground",
};

function getProviderColor(provider: string): string {
  return PROVIDER_COLORS[provider.toLowerCase()] ?? "bg-accent";
}

function getRegionFlag(region: string): string {
  if (region === "eu") return " EU";
  if (region === "local") return " Local";
  return "";
}

export function BentoProviderCosts({
  providerCosts,
  savings,
}: {
  providerCosts: ProviderCostsData | null;
  savings: ProviderSavingsData | null;
}) {
  if (!providerCosts) return null;
  const { by_provider, total_cost_usd, period_days } = providerCosts;
  const topSaving = savings?.alternatives?.[0] ?? null;

  return (
    <div className={cn(
      "rounded-2xl border border-border bg-surface shadow-sm",
      "hover:border-accent/30 transition-settle",
      "p-5 flex flex-col gap-3",
    )}>
      <div className="flex items-center justify-between">
        <h3 className="panel-label text-muted-foreground">Cost by Provider</h3>
        <span className="text-xs text-muted-foreground">Last {period_days} days</span>
      </div>

      {by_provider.length === 0 ? (
        <p className="text-xs text-muted-foreground">No cost data available yet.</p>
      ) : (
        <div className="space-y-3">
          {by_provider.map((p) => (
            <div key={`${p.provider}:${p.model}`}>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-foreground font-medium">
                  {p.provider}
                  <span className="text-muted-foreground font-normal ml-1">
                    {getRegionFlag(p.region)}
                  </span>
                </span>
                <span className="text-muted-foreground tabular-nums">
                  ${p.total_cost_usd.toFixed(2)} ({p.percentage}%)
                </span>
              </div>
              <div className="h-2 rounded-full bg-border overflow-hidden">
                <div
                  className={cn("h-full rounded-full transition-all duration-500", getProviderColor(p.provider))}
                  style={{ width: `${p.percentage}%` }}
                />
              </div>
              <p className="text-[10px] text-muted-foreground mt-0.5">
                {p.run_count.toLocaleString()} steps - ${p.avg_cost_per_run.toFixed(4)} avg
              </p>
            </div>
          ))}
        </div>
      )}

      <div className="pt-1 border-t border-border">
        <p className="text-xs text-muted-foreground">
          Total: <span className="font-semibold text-foreground">${total_cost_usd.toFixed(2)}</span>
        </p>
      </div>

      {topSaving && topSaving.savings_percent > 10 && (
        <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
          <p className="text-xs font-medium text-accent">
            {topSaving.note}
          </p>
          <p className="text-[11px] text-muted-foreground mt-1">
            Estimated savings: ${topSaving.savings_usd.toFixed(0)}/month ({topSaving.savings_percent}%)
          </p>
        </div>
      )}
    </div>
  );
}

export function BentoSavingsOpportunities({ savings }: { savings: ProviderSavingsData }) {
  if (savings.alternatives.length === 0) return null;
  return (
    <div className={cn(
      "rounded-2xl border border-border bg-surface shadow-sm",
      "hover:border-accent/30 transition-settle",
      "p-5 flex flex-col gap-3",
    )}>
      <h3 className="panel-label text-muted-foreground">Savings Opportunities</h3>
      <div className="space-y-3">
        {savings.alternatives.slice(0, 3).map((alt) => (
          <div key={alt.provider} className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-xs font-medium text-foreground">
                {alt.provider.charAt(0).toUpperCase() + alt.provider.slice(1)}
                {alt.region === "eu" && (
                  <span className="ml-1 text-[10px] font-semibold text-success bg-success/10 rounded-full px-1.5 py-0.5">EU</span>
                )}
                {alt.region === "local" && (
                  <span className="ml-1 text-[10px] font-semibold text-muted-foreground bg-border rounded-full px-1.5 py-0.5">Local</span>
                )}
              </p>
              <p className="text-[11px] text-muted-foreground mt-0.5">
                ${alt.projected_cost_usd.toFixed(2)} projected
              </p>
            </div>
            <span className="text-xs font-semibold text-success shrink-0">
              -{alt.savings_percent}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
