import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  ChevronRight,
  FlaskConical,
  GitBranch,
  Layers,
  Moon,
  MoonStar,
  Radio,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { AdapterDrawer } from "@/components/evolution/AdapterDrawer";
import { AdapterLineageGraph } from "@/components/evolution/AdapterLineageGraph";
import { cn, formatRelativeTime } from "@/lib/utils";

// --- Types ---

export interface AdapterInfo {
  adapter_id: string;
  base_model: string;
  metrics: { loss?: number; eval_score?: number };
  samples: number;
  lora_config: Record<string, number | string>;
  dataset_hash: string | null;
  parent_adapter_id: string | null;
  created_at: number; // unix seconds
  served: boolean;
}

export interface SelfTuneNight {
  night: string; // ISO date
  mutations_tried: number;
  mutations_kept: number;
  adapters_produced: number;
  best_eval_score: number | null;
  best_delta: number | null;
  adapter_ids: string[];
}

interface SelfTuneNightsData {
  nights: SelfTuneNight[];
  enabled: boolean;
  total_adapters: number;
}

const DOCS_URL =
  "https://github.com/gizmax/Sandcastle/blob/main/docs/overnight-self-tune-spark.md";

// --- Hero ---

function nightLabel(night: string): string {
  const d = new Date(`${night}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

interface Headline {
  big: string;
  lead: string;
  tail: string;
  sub: string;
}

/** Build the hero headline from the most recent training night. */
function buildHeadline(nights: SelfTuneNight[]): Headline | null {
  const trained = nights.filter((n) => n.adapters_produced > 0);
  const last = trained[trained.length - 1];
  if (!last) return null;

  const sub =
    `Last night shift (${nightLabel(last.night)}): ${last.mutations_tried} fine-tune ` +
    `mutation${last.mutations_tried === 1 ? "" : "s"} tried, ${last.adapters_produced} ` +
    `adapter${last.adapters_produced === 1 ? "" : "s"} trained on your own eval data. ` +
    "All local. All $0.";

  if (last.best_delta != null && last.best_eval_score != null) {
    const prevBest = last.best_eval_score - last.best_delta;
    if (prevBest > 0) {
      const pct = (last.best_delta / prevBest) * 100;
      return {
        big: `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`,
        lead: "Your agent got",
        tail: pct >= 0 ? "better overnight" : "worse overnight - variant discarded",
        sub: `${sub} Best eval score ${prevBest.toFixed(2)} → ${last.best_eval_score.toFixed(2)}.`,
      };
    }
  }

  return {
    big: last.best_eval_score != null ? last.best_eval_score.toFixed(2) : `${last.adapters_produced}`,
    lead: last.best_eval_score != null ? "First night complete - eval score" : "First night complete -",
    tail: last.best_eval_score != null ? "out of the gate" : "adapters trained",
    sub,
  };
}

function HeroDecor() {
  return (
    <>
      <div className="absolute inset-0 bg-grid opacity-40" aria-hidden="true" />
      <div
        className="absolute -top-24 -right-16 h-64 w-64 rounded-full blur-3xl"
        style={{ background: "radial-gradient(circle, rgba(245,158,11,0.18), transparent 70%)" }}
        aria-hidden="true"
      />
      <div
        className="absolute -bottom-32 -left-16 h-72 w-72 rounded-full blur-3xl"
        style={{ background: "radial-gradient(circle, rgba(99,102,241,0.14), transparent 70%)" }}
        aria-hidden="true"
      />
    </>
  );
}

function Eyebrow() {
  return (
    <p className="inline-flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-accent">
      <MoonStar className="h-3.5 w-3.5" />
      Night Shift · Overnight Self-Tune
    </p>
  );
}

function IdleHero({ enabled }: { enabled: boolean }) {
  return (
    <section className="relative overflow-hidden rounded-2xl border border-border bg-surface p-8 sm:p-12 shadow-sm">
      <HeroDecor />
      <div className="relative max-w-2xl">
        <Eyebrow />
        <h2 className="mt-4 text-4xl sm:text-5xl font-bold tracking-tight text-foreground">
          {enabled ? "Night Shift is armed" : "Night Shift is idle"}
        </h2>
        <p className="mt-4 text-sm sm:text-base text-muted leading-relaxed">
          {enabled ? (
            <>Self-tuning is on. The next evolution run will train LoRA adapters on your
            workflow&apos;s own eval data while you sleep - locally, for $0.</>
          ) : (
            <>Enable <code className="rounded-md bg-accent/10 border border-accent/20 px-1.5 py-0.5 font-mono text-xs text-accent">evolution_auto_finetune</code> and
            Sandcastle will train LoRA adapters on your workflow&apos;s own eval data while
            you sleep - locally, for $0. Wake up to a measurably better agent.</>
          )}
        </p>
        <div className="mt-6 flex items-center gap-3">
          <a
            href={DOCS_URL}
            target="_blank"
            rel="noreferrer"
            className={cn(
              "inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2",
              "text-sm font-medium text-accent-foreground hover:bg-accent-hover transition-colors"
            )}
          >
            <BookOpen className="h-4 w-4" />
            Read the docs
          </a>
        </div>

        {/* How it works */}
        <div className="mt-10 grid gap-4 sm:grid-cols-3">
          {[
            { icon: FlaskConical, title: "Evolve", text: "The nightly evolution loop mines your eval results for training pairs." },
            { icon: Moon, title: "Train", text: "A task-specific LoRA adapter is fine-tuned on your own data, on your box." },
            { icon: Radio, title: "Serve", text: "Winning adapters are routed live as adapter/<id> - a $0 local model." },
          ].map(({ icon: Icon, title, text }) => (
            <div key={title} className="rounded-xl border border-border bg-background/40 p-4">
              <Icon className="h-4 w-4 text-accent" />
              <p className="mt-2 text-sm font-semibold text-foreground">{title}</p>
              <p className="mt-1 text-xs text-muted leading-relaxed">{text}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Hero({
  headline,
  nights,
  adapters,
}: {
  headline: Headline;
  nights: SelfTuneNight[];
  adapters: AdapterInfo[];
}) {
  const servedAdapter = adapters.find((a) => a.served) ?? null;
  const trainingNights = nights.filter((n) => n.adapters_produced > 0).length;

  return (
    <section className="relative overflow-hidden rounded-2xl border border-accent/20 bg-surface p-8 sm:p-12 shadow-sm glow-accent">
      <HeroDecor />
      <div className="relative flex flex-col gap-8 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Eyebrow />
          <p className="mt-5 text-lg font-medium text-muted">{headline.lead}</p>
          <p
            className={cn(
              "mt-1 text-6xl sm:text-7xl xl:text-8xl font-bold tracking-tighter font-data leading-none",
              "bg-gradient-to-br from-amber-300 via-accent to-amber-600 bg-clip-text text-transparent"
            )}
          >
            {headline.big}
          </p>
          <p className="mt-2 text-lg font-medium text-foreground">{headline.tail}</p>
          <p className="mt-4 max-w-xl text-sm text-muted leading-relaxed">{headline.sub}</p>
        </div>

        {/* Side stats */}
        <div className="grid shrink-0 grid-cols-3 gap-3 lg:grid-cols-1 lg:w-56">
          <div className="rounded-xl border border-border bg-background/40 px-4 py-3">
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Training nights</p>
            <p className="mt-0.5 text-2xl font-bold text-foreground font-data">{trainingNights}</p>
          </div>
          <div className="rounded-xl border border-border bg-background/40 px-4 py-3">
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Adapters trained</p>
            <p className="mt-0.5 text-2xl font-bold text-foreground font-data">{adapters.length}</p>
          </div>
          <div className={cn(
            "rounded-xl border px-4 py-3",
            servedAdapter ? "border-accent/30 bg-accent/5" : "border-border bg-background/40"
          )}>
            <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Serving now</p>
            {servedAdapter ? (
              <p className="mt-0.5 font-mono text-xs font-semibold text-accent truncate" title={servedAdapter.adapter_id}>
                {servedAdapter.adapter_id}
              </p>
            ) : (
              <p className="mt-0.5 text-xs text-muted">base model</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

// --- Nightly improvement chart ---

function NightlyChart({ nights }: { nights: SelfTuneNight[] }) {
  const data = nights
    .filter((n) => n.best_eval_score != null)
    .map((n) => ({
      night: nightLabel(n.night),
      score: n.best_eval_score,
      delta: n.best_delta,
      adapters: n.adapters_produced,
    }));

  if (data.length === 0) {
    return (
      <p className="flex h-48 items-center justify-center text-xs text-muted">
        Eval scores will chart here after the first training night.
      </p>
    );
  }

  return (
    <div className="h-48" data-testid="nightly-chart">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <defs>
            <linearGradient id="nightShiftScore" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity={0.35} />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" vertical={false} />
          <XAxis dataKey="night" tick={{ fontSize: 11 }} stroke="var(--color-muted)" />
          <YAxis
            tick={{ fontSize: 11 }}
            stroke="var(--color-muted)"
            domain={["auto", "auto"]}
            tickFormatter={(v: number) => v.toFixed(2)}
          />
          <Tooltip
            contentStyle={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={((value: number | undefined) => [
              (value ?? 0).toFixed(3),
              "Best eval score",
            ]) as never}
          />
          <Area
            type="monotone"
            dataKey="score"
            stroke="var(--color-accent)"
            strokeWidth={2}
            fill="url(#nightShiftScore)"
            dot={{ r: 3, fill: "var(--color-accent)", stroke: "var(--color-surface)", strokeWidth: 1 }}
            activeDot={{ r: 5 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// --- Adapter table ---

function AdapterTable({
  adapters,
  onSelect,
}: {
  adapters: AdapterInfo[];
  onSelect: (a: AdapterInfo) => void;
}) {
  const newestFirst = useMemo(
    () => [...adapters].sort((a, b) => b.created_at - a.created_at),
    [adapters]
  );

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm" aria-label="Trained adapters">
        <thead>
          <tr className="border-b border-border bg-background/50">
            <th className="px-4 py-2.5 text-left font-medium text-muted text-xs">Adapter</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted text-xs">Base model</th>
            <th className="px-4 py-2.5 text-right font-medium text-muted text-xs">Samples</th>
            <th className="px-4 py-2.5 text-right font-medium text-muted text-xs">Eval score</th>
            <th className="px-4 py-2.5 text-left font-medium text-muted text-xs">Trained</th>
            <th className="px-4 py-2.5 text-center font-medium text-muted text-xs">Status</th>
            <th className="px-4 py-2.5" />
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {newestFirst.map((a) => (
            <tr
              key={a.adapter_id}
              className="cursor-pointer hover:bg-border/10 transition-colors"
              onClick={() => onSelect(a)}
            >
              <td className="px-4 py-2.5">
                <span className="inline-flex items-center gap-2 min-w-0">
                  <Layers className={cn("h-3.5 w-3.5 shrink-0", a.served ? "text-accent" : "text-muted")} />
                  <span className="font-mono text-xs text-foreground truncate max-w-[220px]">{a.adapter_id}</span>
                </span>
              </td>
              <td className="px-4 py-2.5 font-mono text-xs text-muted">{a.base_model}</td>
              <td className="px-4 py-2.5 text-right text-xs text-muted font-data">{a.samples}</td>
              <td className="px-4 py-2.5 text-right text-xs font-semibold text-success font-data">
                {a.metrics?.eval_score != null ? a.metrics.eval_score.toFixed(2) : "-"}
              </td>
              <td className="px-4 py-2.5 text-xs text-muted">
                {a.created_at > 0 ? formatRelativeTime(new Date(a.created_at * 1000)) : "-"}
              </td>
              <td className="px-4 py-2.5 text-center">
                {a.served ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-accent/15 border border-accent/30 px-2 py-0.5 text-[10px] font-semibold text-accent">
                    <Radio className="h-2.5 w-2.5 animate-pulse" />
                    SERVING
                  </span>
                ) : (
                  <span className="text-[10px] text-muted">archived</span>
                )}
              </td>
              <td className="px-4 py-2.5 text-right">
                <ChevronRight className="h-3.5 w-3.5 text-muted inline-block" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Main Page ---

export default function NightShiftPage() {
  const [adapters, setAdapters] = useState<AdapterInfo[]>([]);
  const [nightsData, setNightsData] = useState<SelfTuneNightsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AdapterInfo | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [adaptersRes, nightsRes] = await Promise.all([
        api.get<AdapterInfo[]>("/adapters"),
        api.get<SelfTuneNightsData>("/self-tune/nights"),
      ]);
      if (!mountedRef.current) return;
      if (adaptersRes.data) setAdapters(adaptersRes.data);
      if (nightsRes.data) setNightsData(nightsRes.data);
    } catch {
      if (!mountedRef.current) return;
      setError("Could not connect to the API server");
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const nights = useMemo(() => nightsData?.nights ?? [], [nightsData]);
  const headline = useMemo(() => buildHeadline(nights), [nights]);
  const selectedParent = selected?.parent_adapter_id
    ? adapters.find((a) => a.adapter_id === selected.parent_adapter_id) ?? null
    : null;

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
          Night Shift
        </h1>
        <div className="rounded-xl border border-error/30 bg-error/5 p-4">
          <p className="text-sm text-error">{error}</p>
          <button
            onClick={() => {
              setLoading(true);
              void fetchData();
            }}
            className="mt-2 text-xs font-medium text-accent hover:text-accent/80 transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const isEmpty = adapters.length === 0 || !headline;

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight text-foreground">
          Night Shift
        </h1>
        <a
          href={DOCS_URL}
          target="_blank"
          rel="noreferrer"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5",
            "text-xs font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          )}
        >
          <BookOpen className="h-3.5 w-3.5" />
          Docs
        </a>
      </div>

      {/* Hero */}
      {isEmpty ? (
        <IdleHero enabled={nightsData?.enabled ?? false} />
      ) : (
        <Hero headline={headline} nights={nights} adapters={adapters} />
      )}

      {!isEmpty && (
        <>
          {/* Lineage + chart */}
          <div className="grid gap-4 sm:gap-6 lg:grid-cols-2">
            <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-foreground">
                  <GitBranch className="h-4 w-4 text-accent" />
                  Adapter lineage
                </h2>
                <span className="text-[10px] text-muted">
                  Each generation is fine-tuned from the last - the serving adapter glows.
                </span>
              </div>
              <AdapterLineageGraph adapters={adapters} onSelect={setSelected} />
            </div>

            <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-foreground">
                  <TrendingUp className="h-4 w-4 text-accent" />
                  Eval score by night
                </h2>
                <span className="text-[10px] text-muted">
                  Best adapter eval score per training night
                </span>
              </div>
              <NightlyChart nights={nights} />
            </div>
          </div>

          {/* Adapter table */}
          <div className="rounded-xl border border-border bg-surface p-5 shadow-sm">
            <h2 className="mb-3 inline-flex items-center gap-2 text-sm font-semibold text-foreground">
              <Sparkles className="h-4 w-4 text-accent" />
              All adapters ({adapters.length})
            </h2>
            <AdapterTable adapters={adapters} onSelect={setSelected} />
          </div>
        </>
      )}

      {/* Detail drawer */}
      {selected && (
        <AdapterDrawer
          adapter={selected}
          parent={selectedParent}
          onSelectParent={setSelected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
