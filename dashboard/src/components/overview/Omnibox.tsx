import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

/** Result shape returned by `POST /generate` (mirrors GenerateModal). */
interface GenerateResult {
  yaml_content: string;
  name: string;
  description: string;
  steps_count: number;
  validation_errors: string[];
  input_schema: Record<string, unknown> | null;
}

/** Rotating placeholder examples — real-world agent tasks. */
const EXAMPLES = [
  "Summarize today's support tickets and post to Slack",
  "Enrich these leads and score them",
  "Research a competitor and draft a battlecard",
  "Review a contract for risky clauses",
  "Turn meeting notes into action items",
  "Monitor a URL and alert me on changes",
];

/** Custom DOM event the CommandPalette dispatches to focus the omnibox. */
export const OMNIBOX_FOCUS_EVENT = "sandcastle:omnibox-focus";

const PLACEHOLDER_ROTATE_MS = 3800;

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

const NO_PROVIDER_MSG =
  "Connect an AI provider to generate workflows — add a key in Settings → Providers, then try again.";

/**
 * Map a structured generate error to a clear, honest message. Generation depends
 * on a model being available; we surface exactly why it failed using the backend
 * error code (with a message-regex fallback) rather than a vague "try again".
 */
function mapGenerateError(error: { code: string; message: string }): string {
  const code = error.code || "";
  const msg = error.message || "";
  if (
    code === "NO_PROVIDER" ||
    code === "MISSING_API_KEY" ||
    /api[_ ]?key|provider|no.*model|unauthor|credential/i.test(msg)
  ) {
    return NO_PROVIDER_MSG;
  }
  if (code === "UPSTREAM_ERROR") {
    return "Your AI provider returned an error. Check its key and region in Settings, then try again.";
  }
  if (code === "GENERATION_FAILED") {
    return "Couldn't build that workflow. Try rephrasing or simplifying the request.";
  }
  if (code === "NETWORK_ERROR" || code.startsWith("HTTP_5")) {
    return "Can't reach the Sandcastle backend. Make sure the server is running, then try again.";
  }
  return msg || "Couldn't generate that workflow. Try rephrasing.";
}

interface OmniboxProps {
  /** Compact spacing for use inside the empty state. */
  variant?: "default" | "empty";
}

/**
 * THE OMNIBOX — "What should your agent do?"
 *
 * A prominent NL input at the top of the Overview. Submitting calls the
 * existing `/generate` flow, then renders a readable preview of the generated
 * workflow with a primary "Run it" CTA and a secondary "Edit" handoff to the
 * visual builder. Refine + validation errors are surfaced inline, reusing the
 * same patterns as GenerateModal.
 */
export function Omnibox({ variant = "default" }: OmniboxProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refineText, setRefineText] = useState("");
  const [refining, setRefining] = useState(false);
  const [placeholderIdx, setPlaceholderIdx] = useState(0);

  const [running, setRunning] = useState(false);

  // Proactive provider availability — generation needs a model. Check once on
  // mount so we can warn the user up front instead of failing only on submit.
  // "unknown" while loading / when the backend is unreachable (the global demo
  // banner already covers that case); "none" only when we positively learn no
  // provider is configured.
  const [providerState, setProviderState] = useState<
    "unknown" | "available" | "none"
  >("unknown");

  useEffect(() => {
    let cancelled = false;
    api
      .get<{ available?: { configured?: boolean; status?: string }[] }>(
        "/advisor/status",
      )
      .then((res) => {
        if (cancelled) return;
        const list = res.data?.available;
        if (!Array.isArray(list)) return; // unknown — don't block
        const ok = list.some((p) => p.configured || p.status === "ok");
        setProviderState(ok ? "available" : "none");
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const noProvider = providerState === "none";

  const reduceMotion = useMemo(() => prefersReducedMotion(), []);

  /** Whether the generated workflow needs user-provided inputs before running. */
  const needsInput = useMemo(() => {
    const schema = result?.input_schema as
      | { properties?: Record<string, unknown> }
      | null
      | undefined;
    return !!schema?.properties && Object.keys(schema.properties).length > 0;
  }, [result]);

  // Rotate the placeholder through real examples (paused for reduced motion).
  useEffect(() => {
    if (reduceMotion) return;
    if (description || result) return; // don't distract while typing/reviewing
    const t = setInterval(() => {
      setPlaceholderIdx((i) => (i + 1) % EXAMPLES.length);
    }, PLACEHOLDER_ROTATE_MS);
    return () => clearInterval(t);
  }, [reduceMotion, description, result]);

  // Listen for the ⌘K "Describe a workflow…" action.
  useEffect(() => {
    const handler = () => {
      inputRef.current?.focus();
      inputRef.current?.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "center",
      });
    };
    window.addEventListener(OMNIBOX_FOCUS_EVENT, handler);
    return () => window.removeEventListener(OMNIBOX_FOCUS_EVENT, handler);
  }, [reduceMotion]);

  const handleGenerate = useCallback(async () => {
    if (!description.trim()) return;
    // No model available — don't even hit the backend; tell the user plainly.
    if (noProvider) {
      setError(NO_PROVIDER_MSG);
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);

    const res = await api.post<GenerateResult>(
      "/generate",
      { description: description.trim() },
      90_000,
    );

    setLoading(false);
    if (res.error) {
      setError(mapGenerateError(res.error));
      return;
    }
    if (res.data) setResult(res.data);
  }, [description, noProvider]);

  const handleRefine = useCallback(async () => {
    if (!refineText.trim() || !result) return;
    setRefining(true);
    setError(null);

    const res = await api.post<GenerateResult>(
      "/generate",
      {
        description: description.trim(),
        refine_from: result.yaml_content,
        refine_instruction: refineText.trim(),
      },
      90_000,
    );

    setRefining(false);
    if (res.error) {
      setError(res.error.message || "Refine failed. Try again.");
      return;
    }
    if (res.data) {
      setResult(res.data);
      setRefineText("");
    }
  }, [refineText, result, description]);

  /**
   * Run it: when the workflow needs inputs, hand off to the builder (which owns
   * the input-schema RunWorkflowModal). Otherwise save + run inline and jump to
   * the live run — same API pattern as WorkflowBuilderPage.
   */
  const handleRunIt = useCallback(async () => {
    if (!result || running) return;

    if (needsInput) {
      navigate("/workflows/builder", {
        state: { yaml: result.yaml_content, autorun: true },
      });
      return;
    }

    setRunning(true);
    // Persist first so the workflow exists, then run it (no inputs required).
    const saveRes = await api.post("/workflows", {
      name: result.name,
      content: result.yaml_content,
    });
    if (saveRes.error) {
      setRunning(false);
      toast.error(`Save failed: ${saveRes.error.message}`);
      return;
    }
    const runRes = await api.post<{ run_id: string }>("/workflows/run", {
      workflow: result.yaml_content,
      input: {},
    });
    setRunning(false);
    if (runRes.error) {
      toast.error(`Run failed: ${runRes.error.message}`);
      return;
    }
    if (runRes.data?.run_id) {
      navigate(`/runs/${runRes.data.run_id}`);
    } else {
      // Fallback: open the builder so the user can run manually.
      navigate("/workflows/builder", { state: { yaml: result.yaml_content } });
    }
  }, [result, running, needsInput, navigate]);

  /** Edit: progressive depth — open the visual/YAML builder with the draft. */
  const handleEdit = useCallback(() => {
    if (!result) return;
    navigate("/workflows/builder", { state: { yaml: result.yaml_content } });
  }, [result, navigate]);

  const handleReset = useCallback(() => {
    setResult(null);
    setError(null);
    setRefineText("");
    setDescription("");
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const hasErrors = result && result.validation_errors.length > 0;

  return (
    <section
      aria-labelledby="omnibox-heading"
      className={cn(
        "bg-surface rounded-2xl shadow-sm border border-border",
        "focus-within:border-accent/40 transition-colors duration-300",
        variant === "empty" ? "p-6 sm:p-7" : "p-6 sm:p-8",
      )}
    >
      <div className="flex items-center gap-2.5 mb-3">
        <div className="flex items-center justify-center w-8 h-8 rounded-xl bg-accent/10">
          <Sparkles className="h-4 w-4 text-accent" />
        </div>
        <div>
          <h2
            id="omnibox-heading"
            className="text-lg sm:text-xl font-bold tracking-tight text-foreground"
          >
            {variant === "empty"
              ? "Describe your first agent"
              : "What should your agent do?"}
          </h2>
          <p className="text-xs text-muted-foreground">
            Describe it in plain English — Sandcastle builds the workflow.
          </p>
        </div>
      </div>

      {/* Persistent provider notice — generation needs a model */}
      {noProvider && !result && (
        <div className="mb-3 flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-warning">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            No AI provider is connected — workflow generation needs a model.{" "}
            <Link
              to="/settings?tab=providers"
              className="font-medium underline underline-offset-2 hover:text-warning/80"
            >
              Add one in Settings → Providers
            </Link>{" "}
            (or run a local model).
          </span>
        </div>
      )}

      {/* Input form */}
      {!result && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void handleGenerate();
          }}
        >
          <label htmlFor="omnibox-input" className="sr-only">
            Describe the task for your agent
          </label>
          <div
            className={cn(
              "flex flex-col gap-3 sm:flex-row sm:items-end",
            )}
          >
            <textarea
              id="omnibox-input"
              ref={inputRef}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={EXAMPLES[placeholderIdx]}
              rows={2}
              disabled={loading}
              aria-describedby="omnibox-hint"
              className={cn(
                "flex-1 rounded-xl border border-border bg-background px-4 py-3 text-sm text-foreground",
                "placeholder:text-muted-foreground/60 resize-none leading-relaxed",
                "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
                "disabled:opacity-60",
              )}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleGenerate();
                }
              }}
            />
            <button
              type="submit"
              disabled={loading || !description.trim() || noProvider}
              title={noProvider ? NO_PROVIDER_MSG : undefined}
              className={cn(
                "inline-flex items-center justify-center gap-2 rounded-xl px-5 py-3 text-sm font-semibold transition-all shrink-0",
                "active:scale-[0.98]",
                loading || !description.trim() || noProvider
                  ? "bg-muted/20 text-muted-foreground cursor-not-allowed"
                  : "bg-accent text-accent-foreground hover:bg-accent-hover shadow-sm hover:shadow-md",
              )}
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Building…
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  Build it
                </>
              )}
            </button>
          </div>
          <p id="omnibox-hint" className="mt-2 text-[11px] text-muted-foreground/70">
            Press Enter to build · Shift+Enter for a new line
          </p>
        </form>
      )}

      {/* Error / calm hint */}
      {error && !result && (
        <div
          role="alert"
          className="mt-4 flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/5 px-4 py-3 text-sm text-warning"
        >
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Loading skeleton hint */}
      {loading && !result && (
        <div className="mt-4 flex items-center gap-2.5 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
          <span>Designing your workflow — this can take a few seconds…</span>
        </div>
      )}

      {/* Result preview */}
      {result && (
        <div className="mt-2 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2 text-sm min-w-0">
              {hasErrors ? (
                <AlertTriangle className="h-4 w-4 text-warning shrink-0" />
              ) : (
                <CheckCircle className="h-4 w-4 text-success shrink-0" />
              )}
              <span className="font-semibold text-foreground truncate">
                {result.name}
              </span>
              <span className="text-xs text-muted-foreground shrink-0">
                · {result.steps_count} step{result.steps_count === 1 ? "" : "s"}
              </span>
            </div>
            <button
              type="button"
              onClick={handleReset}
              aria-label="Start over"
              className="rounded-lg p-1 text-muted-foreground hover:text-foreground transition-colors shrink-0"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {result.description && (
            <p className="text-sm text-muted-foreground -mt-1">
              {result.description}
            </p>
          )}

          {/* Validation issues (calm, with refine affordance below) */}
          {hasErrors && (
            <div className="rounded-xl border border-warning/30 bg-warning/5 px-4 py-3 space-y-1">
              <p className="text-xs font-medium text-warning">
                {result.validation_errors.length} thing
                {result.validation_errors.length === 1 ? "" : "s"} to review:
              </p>
              {result.validation_errors.map((err, i) => (
                <p key={i} className="text-xs text-warning/90 pl-1">
                  · {err}
                </p>
              ))}
            </div>
          )}

          {/* Readable YAML preview */}
          <div className="rounded-xl border border-border bg-background overflow-hidden">
            <div className="flex items-center justify-between border-b border-border px-4 py-2">
              <span className="text-xs font-medium text-muted-foreground">
                Preview
              </span>
              <span className="text-xs text-muted-foreground/60">
                {result.steps_count} step{result.steps_count === 1 ? "" : "s"}
              </span>
            </div>
            <pre className="max-h-56 overflow-auto p-4 text-xs text-foreground/80 font-mono leading-relaxed">
              {result.yaml_content}
            </pre>
          </div>

          {/* Refine */}
          <div className="flex gap-2">
            <label htmlFor="omnibox-refine" className="sr-only">
              Refine this workflow
            </label>
            <input
              id="omnibox-refine"
              type="text"
              value={refineText}
              onChange={(e) => setRefineText(e.target.value)}
              placeholder="Refine: add a review step, use a cheaper model…"
              className={cn(
                "flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm",
                "placeholder:text-muted-foreground/50",
                "focus-visible:border-accent/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/30",
              )}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleRefine();
                }
              }}
            />
            <button
              type="button"
              onClick={() => void handleRefine()}
              disabled={refining || !refineText.trim()}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm font-medium transition-colors",
                refining || !refineText.trim()
                  ? "text-muted-foreground cursor-not-allowed"
                  : "text-muted-foreground hover:text-foreground hover:border-accent",
              )}
            >
              {refining ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              Refine
            </button>
          </div>

          {error && (
            <div role="alert" className="text-sm text-error">
              {error}
            </div>
          )}

          {/* Primary CTA: Run it · Secondary: Edit */}
          <div className="flex flex-col sm:flex-row gap-2 pt-1">
            <button
              type="button"
              onClick={() => void handleRunIt()}
              disabled={running}
              className={cn(
                "inline-flex items-center justify-center gap-2 rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-accent-foreground",
                "hover:bg-accent-hover transition-all shadow-sm hover:shadow-md active:scale-[0.98]",
                running && "opacity-70 cursor-not-allowed",
              )}
            >
              {running ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Starting…
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" />
                  {needsInput ? "Run it (add inputs)" : "Run it"}
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </button>
            <button
              type="button"
              onClick={handleEdit}
              className={cn(
                "inline-flex items-center justify-center gap-2 rounded-xl border border-border px-5 py-2.5 text-sm font-medium text-muted-foreground",
                "hover:text-foreground hover:border-accent/40 transition-colors active:scale-[0.98]",
              )}
            >
              <Pencil className="h-4 w-4" />
              Edit in builder
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
