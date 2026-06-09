import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { POLL_INTERVAL } from "@/lib/constants";
import {
  Sparkles,
  TrendingUp,
  TrendingDown,
  X,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Loader2,
  Ban,
  BookOpen,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { toast } from "sonner";
import { api } from "@/api/client";
import { EmptyState } from "@/components/shared/EmptyState";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { formatCost, formatRelativeTime, cn } from "@/lib/utils";

// --- Types ---

interface Evolution {
  id: string;
  workflow_name: string;
  status: string;
  optimize_for: string;
  baseline_score: number;
  best_score: number;
  baseline_quality: number;
  best_quality: number;
  baseline_cost: number;
  best_cost: number;
  max_iterations: number;
  current_iteration: number;
  total_keeps: number;
  total_discards: number;
  created_at: string | null;
  completed_at: string | null;
}

interface Iteration {
  iteration_number: number;
  mutation_type: string;
  mutation_description: string;
  score: number;
  quality: number;
  cost_usd: number;
  status: "keep" | "discard";
}

interface EvolutionDetail extends Evolution {
  iterations: Iteration[];
}

interface EvolutionStats {
  total_evolutions: number;
  running_evolutions: number;
  completed_evolutions: number;
  avg_score_improvement: number;
  avg_cost_savings_pct: number;
  total_iterations_run: number;
}

// --- Helpers ---

const MUTATION_TYPE_LABEL: Record<string, string> = {
  model: "Model swap",
  prompt: "Prompt edit",
  simplify: "Simplify",
};

const MUTATION_TYPE_COLOR: Record<string, string> = {
  model: "bg-accent/15 text-accent border-accent/30",
  prompt: "bg-running/15 text-running border-running/30",
  simplify: "bg-success/15 text-success border-success/30",
};

const OPTIMIZE_LABELS: Record<string, string> = {
  quality: "Quality",
  cost: "Cost",
  latency: "Latency",
  balanced: "Balanced",
};

function ScoreDelta({ baseline, best }: { baseline: number; best: number }) {
  const delta = best - baseline;
  const pct = baseline > 0 ? ((delta / baseline) * 100).toFixed(1) : "0.0";
  const positive = delta >= 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 font-semibold",
        positive ? "text-success" : "text-error"
      )}
    >
      {positive ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
      {positive ? "+" : ""}{pct}%
    </span>
  );
}

// --- Example eval suites ---

const EXAMPLE_EVAL_SUITES: { label: string; description: string; yaml: string }[] = [
  {
    label: "Quality check",
    description: "Basic pass/fail assertions",
    yaml: `suite:
  name: quality-check
  description: Verify output correctness with pass/fail assertions
  cases:
    - name: basic-output-valid
      input:
        query: "Summarize the benefits of automation"
      assertions:
        - type: contains
          value: "efficiency"
        - type: min_length
          value: 50
    - name: no-hallucination
      input:
        query: "What is 2 + 2?"
      assertions:
        - type: equals
          value: "4"
        - type: max_length
          value: 10`,
  },
  {
    label: "Cost optimization",
    description: "Output quality while minimizing cost",
    yaml: `suite:
  name: cost-optimization
  description: Maintain quality while reducing token usage and cost
  cases:
    - name: concise-response
      input:
        query: "Explain quantum computing in one paragraph"
      assertions:
        - type: max_tokens
          value: 200
        - type: contains
          value: "qubit"
        - type: min_quality
          value: 0.7
    - name: efficient-extraction
      input:
        query: "Extract the main topic from: AI is transforming healthcare"
      assertions:
        - type: max_tokens
          value: 20
        - type: contains
          value: "healthcare"`,
  },
  {
    label: "Accuracy benchmark",
    description: "Detailed accuracy scoring with multiple test cases",
    yaml: `suite:
  name: accuracy-benchmark
  description: Multi-case accuracy evaluation with weighted scoring
  scoring:
    method: weighted_average
  cases:
    - name: factual-recall
      weight: 2.0
      input:
        query: "What is the capital of France?"
      assertions:
        - type: equals
          value: "Paris"
        - type: max_latency_ms
          value: 3000
    - name: reasoning-task
      weight: 3.0
      input:
        query: "If a train travels 60 km/h for 2.5 hours, how far does it go?"
      assertions:
        - type: contains
          value: "150"
        - type: min_quality
          value: 0.9
    - name: edge-case-handling
      weight: 1.0
      input:
        query: ""
      assertions:
        - type: not_empty
        - type: contains
          value: "provide"`,
  },
];

const DEFAULT_EVAL_SUITE = "suite:\n  name: my-eval\n  cases:\n    - name: baseline\n      input: {}";

// --- Start Evolution Modal ---

interface StartModalProps {
  initialWorkflow?: string;
  onClose: () => void;
  onStart: (data: {
    workflow_name: string;
    eval_suite: string;
    optimize_for: string;
    max_iterations: number;
    budget_limit?: number;
  }) => Promise<void>;
  workflows: string[];
}

function StartEvolutionModal({ initialWorkflow, onClose, onStart, workflows }: StartModalProps) {
  const [workflowName, setWorkflowName] = useState(initialWorkflow ?? "");
  const [evalSuite, setEvalSuite] = useState(DEFAULT_EVAL_SUITE);
  const [optimizeFor, setOptimizeFor] = useState<"quality" | "cost" | "latency" | "balanced">("balanced");
  const [maxIterations, setMaxIterations] = useState(20);
  const [budgetLimit, setBudgetLimit] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [exampleMenuOpen, setExampleMenuOpen] = useState(false);
  const exampleMenuRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (exampleMenuRef.current && !exampleMenuRef.current.contains(event.target as Node)) {
        setExampleMenuOpen(false);
      }
    }
    if (exampleMenuOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [exampleMenuOpen]);

  const loadExample = (yaml: string) => {
    const hasUserContent = evalSuite.trim() !== "" && evalSuite.trim() !== DEFAULT_EVAL_SUITE.trim();
    if (hasUserContent) {
      const confirmed = window.confirm(
        "The eval suite textarea already has content. Replace it with the example?"
      );
      if (!confirmed) {
        setExampleMenuOpen(false);
        return;
      }
    }
    setEvalSuite(yaml);
    setExampleMenuOpen(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!workflowName.trim()) {
      toast.error("Please select a workflow");
      return;
    }
    setSubmitting(true);
    try {
      await onStart({
        workflow_name: workflowName,
        eval_suite: evalSuite,
        optimize_for: optimizeFor,
        max_iterations: maxIterations,
        budget_limit: budgetLimit ? parseFloat(budgetLimit) : undefined,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-border bg-surface shadow-2xl mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-4">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <h2 className="text-base font-semibold text-foreground">Start Evolution</h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close modal"
            className="rounded-md p-1 text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={(e) => { void handleSubmit(e); }} className="p-5 space-y-4">
          {/* Workflow selector */}
          <div>
            <label className="block text-xs font-medium text-foreground mb-1.5">
              Workflow
            </label>
            {workflows.length > 0 ? (
              <select
                value={workflowName}
                onChange={(e) => setWorkflowName(e.target.value)}
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2",
                  "text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent/40"
                )}
              >
                <option value="">Select a workflow...</option>
                {workflows.map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                value={workflowName}
                onChange={(e) => setWorkflowName(e.target.value)}
                placeholder="workflow-name"
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2",
                  "text-sm text-foreground placeholder-muted focus:outline-none focus:ring-1 focus:ring-accent/40"
                )}
              />
            )}
          </div>

          {/* Eval suite */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="block text-xs font-medium text-foreground">
                Eval Suite (YAML)
              </label>
              <div className="relative" ref={exampleMenuRef}>
                <button
                  type="button"
                  onClick={() => setExampleMenuOpen((prev) => !prev)}
                  className={cn(
                    "inline-flex items-center gap-1 rounded-md border border-border px-2 py-1",
                    "text-[11px] font-medium text-muted hover:text-foreground hover:bg-border/40 transition-colors"
                  )}
                >
                  <BookOpen className="h-3 w-3" />
                  Load example
                  <ChevronDown className={cn("h-3 w-3 transition-transform", exampleMenuOpen && "rotate-180")} />
                </button>
                {exampleMenuOpen && (
                  <div className="absolute right-0 top-full mt-1 z-10 w-56 rounded-lg border border-border bg-surface shadow-lg overflow-hidden">
                    {EXAMPLE_EVAL_SUITES.map((example) => (
                      <button
                        key={example.label}
                        type="button"
                        onClick={() => loadExample(example.yaml)}
                        className="w-full text-left px-3 py-2 hover:bg-border/20 transition-colors"
                      >
                        <p className="text-xs font-medium text-foreground">{example.label}</p>
                        <p className="text-[10px] text-muted">{example.description}</p>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <textarea
              value={evalSuite}
              onChange={(e) => setEvalSuite(e.target.value)}
              rows={5}
              className={cn(
                "w-full rounded-lg border border-border bg-background px-3 py-2",
                "font-mono text-xs text-foreground placeholder-muted",
                "focus:outline-none focus:ring-1 focus:ring-accent/40 resize-none"
              )}
            />
            <p className="mt-1 text-[10px] text-muted">
              Define test cases to evaluate each mutation. Need help? Load an example above.
            </p>
          </div>

          {/* Optimize for */}
          <div>
            <label className="block text-xs font-medium text-foreground mb-2">
              Optimize for
            </label>
            <div className="grid grid-cols-4 gap-2">
              {(["quality", "cost", "latency", "balanced"] as const).map((opt) => (
                <label
                  key={opt}
                  className={cn(
                    "flex items-center justify-center rounded-lg border px-3 py-2 cursor-pointer text-xs font-medium transition-colors",
                    optimizeFor === opt
                      ? "border-accent bg-accent/10 text-accent"
                      : "border-border text-muted hover:text-foreground hover:border-border/70"
                  )}
                >
                  <input
                    type="radio"
                    name="optimize_for"
                    value={opt}
                    checked={optimizeFor === opt}
                    onChange={() => setOptimizeFor(opt)}
                    className="sr-only"
                  />
                  {OPTIMIZE_LABELS[opt]}
                </label>
              ))}
            </div>
          </div>

          {/* Max iterations + Budget */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-foreground mb-1.5">
                Max Iterations
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={maxIterations}
                onChange={(e) => setMaxIterations(parseInt(e.target.value, 10) || 20)}
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2",
                  "text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent/40"
                )}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-foreground mb-1.5">
                Budget Limit (USD, optional)
              </label>
              <input
                type="number"
                min={0}
                step={0.01}
                value={budgetLimit}
                onChange={(e) => setBudgetLimit(e.target.value)}
                placeholder="e.g. 5.00"
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2",
                  "text-sm text-foreground placeholder-muted focus:outline-none focus:ring-1 focus:ring-accent/40"
                )}
              />
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className={cn(
                "rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted",
                "hover:text-foreground hover:bg-border/40 transition-colors"
              )}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-background",
                "hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {submitting ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              Start Evolution
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// --- Evolution Detail Panel ---

function EvolutionDetail({
  evolution,
  onAccept,
  onClose,
  actionLoading,
}: {
  evolution: EvolutionDetail;
  onAccept: (id: string) => Promise<void>;
  onClose: () => void;
  actionLoading: boolean;
}) {
  const scoreImprovement = evolution.baseline_score > 0
    ? (((evolution.best_score - evolution.baseline_score) / evolution.baseline_score) * 100).toFixed(1)
    : "0.0";
  const costSavingsPct = evolution.baseline_cost > 0
    ? (((evolution.baseline_cost - evolution.best_cost) / evolution.baseline_cost) * 100).toFixed(1)
    : "0.0";

  const chartData = evolution.iterations.map((it) => ({
    iteration: it.iteration_number,
    score: it.score,
    baseline: evolution.baseline_score,
  }));

  return (
    <div className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden">
      {/* Detail header */}
      <div className="flex items-center justify-between border-b border-border px-5 py-3.5">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-accent" />
          <h2 className="text-sm font-semibold text-foreground">{evolution.workflow_name}</h2>
          <span className="text-xs text-muted">/</span>
          <span className="text-xs text-muted capitalize">{evolution.optimize_for}</span>
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-1 text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          aria-label="Close detail"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="p-5 space-y-6">
        {/* Baseline vs Best comparison */}
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-lg border border-border bg-background/40 p-4">
            <p className="text-xs font-medium text-muted-foreground mb-3">Baseline</p>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">Score</span>
                <span className="text-sm font-semibold text-foreground">{evolution.baseline_score.toFixed(1)}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">Quality</span>
                <span className="text-sm font-medium text-foreground">{(evolution.baseline_quality * 100).toFixed(0)}%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">Cost/run</span>
                <span className="text-sm font-medium text-foreground">{formatCost(evolution.baseline_cost)}</span>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-accent/30 bg-accent/5 p-4">
            <p className="text-xs font-medium text-accent mb-3">Best Variant</p>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">Score</span>
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-semibold text-foreground">{evolution.best_score.toFixed(1)}</span>
                  <ScoreDelta baseline={evolution.baseline_score} best={evolution.best_score} />
                </div>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">Quality</span>
                <span className="text-sm font-medium text-success">{(evolution.best_quality * 100).toFixed(0)}%</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted">Cost/run</span>
                <span className="text-sm font-medium text-success">{formatCost(evolution.best_cost)}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Summary stats */}
        <div className="flex items-center gap-6 text-sm">
          <span className="text-muted">
            Score improvement: <span className="font-semibold text-success">+{scoreImprovement}%</span>
          </span>
          <span className="text-muted">
            Cost savings: <span className="font-semibold text-accent">-{costSavingsPct}%</span>
          </span>
          <span className="text-muted">
            Keeps: <span className="font-medium text-foreground">{evolution.total_keeps}</span>
          </span>
          <span className="text-muted">
            Discards: <span className="font-medium text-foreground">{evolution.total_discards}</span>
          </span>
        </div>

        {/* Score evolution chart */}
        {chartData.length > 0 && (
          <div>
            <h3 className="text-xs font-medium text-muted-foreground mb-3">Score Evolution</h3>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                  <XAxis
                    dataKey="iteration"
                    tick={{ fontSize: 11 }}
                    stroke="var(--color-muted)"
                    label={{ value: "Iteration", position: "insideBottom", offset: -2, fontSize: 11, fill: "var(--color-muted)" }}
                  />
                  <YAxis
                    tick={{ fontSize: 11 }}
                    stroke="var(--color-muted)"
                    domain={["auto", "auto"]}
                  />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-surface)",
                      border: "1px solid var(--color-border)",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    formatter={((value: number | undefined, name: string | undefined) => [
                      (value ?? 0).toFixed(1),
                      name === "score" ? "Score" : "Baseline",
                    ]) as never}
                  />
                  <ReferenceLine
                    y={evolution.baseline_score}
                    stroke="var(--color-muted)"
                    strokeDasharray="4 2"
                    label={{ value: "Baseline", fontSize: 10, fill: "var(--color-muted)" }}
                  />
                  <Line
                    type="monotone"
                    dataKey="score"
                    stroke="var(--color-accent)"
                    strokeWidth={2}
                    dot={(props) => {
                      const iter = evolution.iterations[props.index];
                      if (!iter) return <g key={props.index} />;
                      const isKeep = iter.status === "keep";
                      return (
                        <circle
                          key={props.index}
                          cx={props.cx}
                          cy={props.cy}
                          r={isKeep ? 4 : 2.5}
                          fill={isKeep ? "var(--color-success)" : "var(--color-error)"}
                          stroke="var(--color-surface)"
                          strokeWidth={1}
                        />
                      );
                    }}
                    activeDot={{ r: 5 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="text-[10px] text-muted mt-1">
              Green dots = kept iterations, red dots = discarded
            </p>
          </div>
        )}

        {/* Iteration table */}
        <div>
          <h3 className="text-xs font-medium text-muted-foreground mb-3">Iteration History</h3>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm" aria-label="Evolution iteration history">
              <thead>
                <tr className="border-b border-border bg-background/50">
                  <th className="px-4 py-2.5 text-left font-medium text-muted text-xs">#</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted text-xs">Type</th>
                  <th className="px-4 py-2.5 text-left font-medium text-muted text-xs">Description</th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted text-xs">Score</th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted text-xs">Quality</th>
                  <th className="px-4 py-2.5 text-right font-medium text-muted text-xs">Cost</th>
                  <th className="px-4 py-2.5 text-center font-medium text-muted text-xs">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {evolution.iterations.map((it) => (
                  <tr
                    key={it.iteration_number}
                    className={cn(
                      "transition-colors",
                      it.status === "keep" ? "hover:bg-success/5" : "hover:bg-border/10 opacity-75"
                    )}
                  >
                    <td className="px-4 py-2.5 text-muted text-xs font-mono">{it.iteration_number}</td>
                    <td className="px-4 py-2.5">
                      <span
                        className={cn(
                          "inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-semibold",
                          MUTATION_TYPE_COLOR[it.mutation_type] ?? "bg-muted/15 text-muted border-muted/30"
                        )}
                      >
                        {MUTATION_TYPE_LABEL[it.mutation_type] ?? it.mutation_type}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-foreground max-w-xs truncate" title={it.mutation_description}>
                      {it.mutation_description}
                    </td>
                    <td className="px-4 py-2.5 text-right text-xs font-medium text-foreground">{it.score.toFixed(1)}</td>
                    <td className="px-4 py-2.5 text-right text-xs text-muted">{(it.quality * 100).toFixed(0)}%</td>
                    <td className="px-4 py-2.5 text-right text-xs text-muted">{formatCost(it.cost_usd)}</td>
                    <td className="px-4 py-2.5 text-center">
                      {it.status === "keep" ? (
                        <CheckCircle2 className="h-4 w-4 text-success inline-block" />
                      ) : (
                        <XCircle className="h-4 w-4 text-error inline-block" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Accept button */}
        {evolution.status === "completed" && (
          <div className="flex justify-end">
            <button
              onClick={() => { void onAccept(evolution.id); }}
              disabled={actionLoading}
              className={cn(
                "inline-flex items-center gap-2 rounded-lg bg-success/10 border border-success/30 px-4 py-2",
                "text-sm font-medium text-success hover:bg-success/20 transition-colors",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
            >
              {actionLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5" />
              )}
              Accept Best Variant
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Main Page ---

export default function EvolutionPage() {
  const location = useLocation();
  const locationState = location.state as { workflow?: string } | null;

  const [evolutions, setEvolutions] = useState<Evolution[]>([]);
  const [stats, setStats] = useState<EvolutionStats | null>(null);
  const [workflows, setWorkflows] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailData, setDetailData] = useState<EvolutionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showStartModal, setShowStartModal] = useState(!!locationState?.workflow);
  const [actionLoading, setActionLoading] = useState<Set<string>>(new Set());
  const mountedRef = useRef(true);
  // Use a ref so action handlers can check without re-creating callbacks
  const actionLoadingRef = useRef(actionLoading);
  actionLoadingRef.current = actionLoading;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const fetchData = useCallback(async () => {
    try {
      setError(null);
      const [evoRes, statsRes, wfRes] = await Promise.all([
        api.get<Evolution[]>("/evolution"),
        api.get<EvolutionStats>("/evolution/stats"),
        api.get<{ name: string }[]>("/workflows"),
      ]);
      if (!mountedRef.current) return;
      if (evoRes.data) setEvolutions(evoRes.data);
      if (statsRes.data) setStats(statsRes.data);
      if (wfRes.data) setWorkflows(wfRes.data.map((w) => w.name));
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

  // Poll for progress when any evolution is running
  const hasRunning = evolutions.some((e) => e.status === "running");
  useEffect(() => {
    if (!hasRunning) return;
    const interval = setInterval(() => { void fetchData(); }, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [hasRunning, fetchData]);

  const handleExpand = useCallback(
    async (evo: Evolution) => {
      const isExpanded = expandedId === evo.id;
      if (isExpanded) {
        setExpandedId(null);
        setDetailData(null);
        return;
      }
      setExpandedId(evo.id);
      setDetailData(null);
      setDetailLoading(true);
      try {
        const res = await api.get<EvolutionDetail>(`/evolution/${evo.id}/status`);
        if (!mountedRef.current) return;
        if (res.data) setDetailData(res.data);
      } finally {
        if (mountedRef.current) setDetailLoading(false);
      }
    },
    [expandedId]
  );

  const handleAccept = useCallback(
    async (id: string) => {
      if (actionLoadingRef.current.has(id)) return;
      setActionLoading((prev) => new Set(prev).add(id));
      try {
        const res = await api.post(`/evolution/${id}/accept`);
        if (!mountedRef.current) return;
        if (res.error) {
          toast.error(`Failed to accept variant: ${res.error.message}`);
          return;
        }
        toast.success("Best variant accepted - workflow updated");
        void fetchData();
        setExpandedId(null);
        setDetailData(null);
      } finally {
        if (mountedRef.current) {
          setActionLoading((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        }
      }
    },
    [fetchData]
  );

  const handleCancel = useCallback(
    async (id: string) => {
      if (actionLoadingRef.current.has(id)) return;
      setActionLoading((prev) => new Set(prev).add(id));
      try {
        const res = await api.post(`/evolution/${id}/cancel`);
        if (!mountedRef.current) return;
        if (res.error) {
          toast.error(`Failed to cancel evolution: ${res.error.message}`);
          return;
        }
        toast.success("Evolution cancelled");
        void fetchData();
      } finally {
        if (mountedRef.current) {
          setActionLoading((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        }
      }
    },
    [fetchData]
  );

  const handleStart = useCallback(
    async (data: {
      workflow_name: string;
      eval_suite: string;
      optimize_for: string;
      max_iterations: number;
      budget_limit?: number;
    }) => {
      const res = await api.post<Evolution>("/evolution/start", data);
      if (res.error) {
        toast.error(`Failed to start evolution: ${res.error.message}`);
        return;
      }
      toast.success(`Evolution started for ${data.workflow_name}`);
      setShowStartModal(false);
      void fetchData();
    },
    [fetchData]
  );

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
        <h1 className="mb-4 sm:mb-6 text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
          Workflow Evolution
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

  const running = evolutions.filter((e) => e.status === "running");
  const completed = evolutions.filter((e) => e.status === "completed" || e.status === "accepted");

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl sm:text-2xl font-semibold font-display tracking-tight text-foreground">
          Workflow Evolution
        </h1>
        <button
          onClick={() => setShowStartModal(true)}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2",
            "text-sm font-medium text-background hover:bg-accent/90 transition-colors"
          )}
        >
          <Sparkles className="h-4 w-4" />
          Start Evolution
        </button>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 lg:grid-cols-6">
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Total</p>
            <p className="mt-1 text-2xl font-semibold text-foreground">{stats.total_evolutions}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Running</p>
            <p className="mt-1 text-2xl font-semibold text-running">{stats.running_evolutions}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Completed</p>
            <p className="mt-1 text-2xl font-semibold text-success">{stats.completed_evolutions}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Avg Score Gain</p>
            <div className="mt-1 flex items-center gap-1">
              <TrendingUp className="h-4 w-4 text-success" />
              <p className="text-2xl font-semibold text-success">+{stats.avg_score_improvement.toFixed(0)}%</p>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Cost Savings</p>
            <div className="mt-1 flex items-center gap-1">
              <TrendingDown className="h-4 w-4 text-accent" />
              <p className="text-2xl font-semibold text-accent">-{stats.avg_cost_savings_pct.toFixed(0)}%</p>
            </div>
          </div>
          <div className="rounded-xl border border-border bg-surface p-4 shadow-sm">
            <p className="text-xs font-medium text-muted-foreground">Iterations Run</p>
            <p className="mt-1 text-2xl font-semibold text-foreground">{stats.total_iterations_run}</p>
          </div>
        </div>
      )}

      {/* Active Experiments */}
      {running.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">
            Active Experiments ({running.length})
          </h2>
          {running.map((evo) => {
            const progressPct = evo.max_iterations > 0
              ? Math.round((evo.current_iteration / evo.max_iterations) * 100)
              : 0;
            const isExpanded = expandedId === evo.id;

            return (
              <div
                key={evo.id}
                className="rounded-xl border border-running/30 bg-running/5 shadow-sm overflow-hidden"
              >
                {/* Card header */}
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-running/10 transition-colors focus-visible:outline-none focus-visible:bg-running/10"
                  onClick={() => { void handleExpand(evo); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      void handleExpand(evo);
                    }
                  }}
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-running/30 bg-running/15">
                    <Sparkles className="h-5 w-5 text-running animate-pulse" />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5">
                      <p className="text-sm font-medium text-foreground">{evo.workflow_name}</p>
                      <span className="inline-flex items-center gap-1 rounded-full bg-running/15 border border-running/30 px-2 py-0.5 text-[10px] font-semibold text-running">
                        <span className="h-1.5 w-1.5 rounded-full bg-running animate-pulse" />
                        Running
                      </span>
                    </div>
                    {/* Progress bar */}
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 rounded-full bg-border/40 overflow-hidden">
                        <div
                          className="h-full rounded-full bg-running transition-all"
                          style={{ width: `${progressPct}%` }}
                        />
                      </div>
                      <span className="text-xs text-muted shrink-0">
                        {evo.current_iteration}/{evo.max_iterations}
                      </span>
                    </div>
                  </div>

                  {/* Score readout */}
                  <div className="hidden sm:flex items-center gap-6 text-right">
                    <div>
                      <p className="text-[10px] text-muted-foreground">Best Score</p>
                      <p className="text-lg font-bold text-foreground">{evo.best_score.toFixed(1)}</p>
                      <p className="text-[10px] text-muted">
                        vs {evo.baseline_score.toFixed(1)} baseline
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted-foreground">Keeps</p>
                      <p className="text-lg font-bold text-success">{evo.total_keeps}</p>
                    </div>
                    <div>
                      <p className="text-[10px] text-muted-foreground">Discards</p>
                      <p className="text-lg font-bold text-muted">{evo.total_discards}</p>
                    </div>
                  </div>

                  {/* Cancel button */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleCancel(evo.id);
                    }}
                    disabled={actionLoading.has(evo.id)}
                    className={cn(
                      "inline-flex items-center gap-1 rounded-md border border-error/30 px-2.5 py-1.5",
                      "text-xs font-medium text-error hover:bg-error/10 transition-colors",
                      "disabled:opacity-50 disabled:cursor-not-allowed"
                    )}
                    aria-label="Cancel evolution"
                  >
                    <Ban className="h-3 w-3" />
                    Cancel
                  </button>

                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted shrink-0" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted shrink-0" />
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="border-t border-running/20 p-5">
                    {detailLoading ? (
                      <div className="flex items-center justify-center py-8">
                        <LoadingSpinner />
                      </div>
                    ) : detailData ? (
                      <EvolutionDetail
                        evolution={detailData}
                        onAccept={handleAccept}
                        onClose={() => {
                          setExpandedId(null);
                          setDetailData(null);
                        }}
                        actionLoading={actionLoading.has(evo.id)}
                      />
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Completed Experiments */}
      {completed.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-foreground">
            Completed ({completed.length})
          </h2>
          {completed.map((evo) => {
            const scoreImprovePct = evo.baseline_score > 0
              ? (((evo.best_score - evo.baseline_score) / evo.baseline_score) * 100).toFixed(1)
              : "0.0";
            const costSavingsPct = evo.baseline_cost > 0
              ? (((evo.baseline_cost - evo.best_cost) / evo.baseline_cost) * 100).toFixed(1)
              : "0.0";
            const isExpanded = expandedId === evo.id;
            const isAccepted = evo.status === "accepted";

            return (
              <div
                key={evo.id}
                className="rounded-xl border border-border bg-surface shadow-sm overflow-hidden"
              >
                {/* Card header */}
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={isExpanded}
                  className="flex items-center gap-4 px-5 py-4 cursor-pointer hover:bg-border/10 transition-colors focus-visible:outline-none focus-visible:bg-border/10"
                  onClick={() => { void handleExpand(evo); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      void handleExpand(evo);
                    }
                  }}
                >
                  <div className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border",
                    isAccepted
                      ? "border-success/30 bg-success/15"
                      : "border-border bg-background/50"
                  )}>
                    <Sparkles className={cn("h-5 w-5", isAccepted ? "text-success" : "text-accent")} />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-medium text-foreground">{evo.workflow_name}</p>
                      {isAccepted && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-success/15 border border-success/30 px-2 py-0.5 text-[10px] font-semibold text-success">
                          <CheckCircle2 className="h-3 w-3" />
                          Accepted
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 mt-1">
                      <span className="text-xs text-muted capitalize">
                        Optimize: <span className="font-medium text-foreground">{OPTIMIZE_LABELS[evo.optimize_for] ?? evo.optimize_for}</span>
                      </span>
                      <span className="text-xs text-muted">
                        {evo.max_iterations} iterations
                      </span>
                      {evo.completed_at && (
                        <span className="text-xs text-muted">{formatRelativeTime(evo.completed_at)}</span>
                      )}
                    </div>
                  </div>

                  {/* Score + cost badges */}
                  <div className="hidden sm:flex items-center gap-3">
                    <div className="text-right">
                      <p className="text-[10px] text-muted-foreground">Score improvement</p>
                      <p className="text-sm font-semibold text-success">+{scoreImprovePct}%</p>
                    </div>
                    <div className="text-right">
                      <p className="text-[10px] text-muted-foreground">Cost savings</p>
                      <p className="text-sm font-semibold text-accent">-{costSavingsPct}%</p>
                    </div>
                  </div>

                  {/* Action buttons */}
                  <div className="flex items-center gap-2">
                    {!isAccepted && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleAccept(evo.id);
                        }}
                        disabled={actionLoading.has(evo.id)}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-md bg-success/10 border border-success/30 px-2.5 py-1.5",
                          "text-xs font-medium text-success hover:bg-success/20 transition-colors",
                          "disabled:opacity-50 disabled:cursor-not-allowed"
                        )}
                      >
                        {actionLoading.has(evo.id) ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <CheckCircle2 className="h-3 w-3" />
                        )}
                        Accept
                      </button>
                    )}
                  </div>

                  {isExpanded ? (
                    <ChevronDown className="h-4 w-4 text-muted shrink-0" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted shrink-0" />
                  )}
                </div>

                {/* Expanded detail */}
                {isExpanded && (
                  <div className="border-t border-border p-5">
                    {detailLoading ? (
                      <div className="flex items-center justify-center py-8">
                        <LoadingSpinner />
                      </div>
                    ) : detailData ? (
                      <EvolutionDetail
                        evolution={detailData}
                        onAccept={handleAccept}
                        onClose={() => {
                          setExpandedId(null);
                          setDetailData(null);
                        }}
                        actionLoading={actionLoading.has(evo.id)}
                      />
                    ) : null}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Empty state */}
      {evolutions.length === 0 && (
        <EmptyState
          icon={Sparkles}
          title="No evolutions yet"
          description="Start an evolution to automatically discover better prompts, models, and configurations for your workflows."
        />
      )}

      {/* Start Evolution Modal */}
      {showStartModal && (
        <StartEvolutionModal
          initialWorkflow={locationState?.workflow ?? ""}
          onClose={() => setShowStartModal(false)}
          onStart={handleStart}
          workflows={workflows}
        />
      )}
    </div>
  );
}
