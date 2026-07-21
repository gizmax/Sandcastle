import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkles, X, Send } from "lucide-react";
import { api } from "@/api/client";
import { cn, formatDuration, formatCost } from "@/lib/utils";

interface Step {
  step_id: string;
  parallel_index: number | null;
  status: string;
  output: unknown;
  cost_usd: number;
  duration_seconds: number;
  attempt: number;
  error: string | null;
  started_at: string | null;
  pdf_artifact?: boolean;
}

interface RunData {
  run_id: string;
  workflow_name: string;
  status: string;
  input_data: Record<string, unknown> | null;
  outputs: Record<string, unknown> | null;
  total_cost_usd: number;
  max_cost_usd: number | null;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  steps: Step[] | null;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: Date;
}

function generateResponse(question: string, run: RunData): string {
  const q = question.toLowerCase();
  const steps = run.steps || [];
  const failedSteps = steps.filter((s) => s.status === "failed");
  const completedSteps = steps.filter((s) => s.status === "completed");

  if (/why|fail|error/.test(q)) {
    if (run.status === "failed" || failedSteps.length > 0) {
      const lines: string[] = [];
      if (run.error) {
        lines.push(`**Run-level error:** ${run.error}`);
      }
      if (failedSteps.length > 0) {
        lines.push(
          `${failedSteps.length} step(s) failed:`
        );
        for (const s of failedSteps) {
          const errMsg = s.error || "No error message captured";
          lines.push(`- **${s.step_id}** (attempt ${s.attempt}): ${errMsg}`);
        }
      }
      if (lines.length === 0) {
        lines.push("The run is marked as failed but no specific error details were captured.");
      }
      return lines.join("\n\n");
    }
    return "This run completed successfully - no errors were found.";
  }

  if (/summary|what happened|summarize|overview/.test(q)) {
    const lines: string[] = [];
    lines.push(`**Workflow:** ${run.workflow_name}`);
    lines.push(`**Status:** ${run.status}`);
    lines.push(`**Total cost:** ${formatCost(run.total_cost_usd)}`);
    if (steps.length > 0) {
      lines.push(`**Steps:** ${steps.length} total - ${completedSteps.length} completed, ${failedSteps.length} failed`);
      lines.push("");
      lines.push("Step breakdown:");
      for (const s of steps) {
        const dur = s.duration_seconds > 0 ? ` (${formatDuration(s.duration_seconds)})` : "";
        const cost = s.cost_usd > 0 ? ` - ${formatCost(s.cost_usd)}` : "";
        lines.push(`- **${s.step_id}**: ${s.status}${dur}${cost}`);
      }
    }
    return lines.join("\n");
  }

  if (/cost|expensive|price|budget|spend/.test(q)) {
    if (steps.length === 0) {
      return `Total run cost: ${formatCost(run.total_cost_usd)}. No step-level breakdown available.`;
    }
    const sorted = [...steps].sort((a, b) => b.cost_usd - a.cost_usd);
    const lines: string[] = [];
    lines.push(`**Total cost:** ${formatCost(run.total_cost_usd)}`);
    if (run.max_cost_usd) {
      const pct = ((run.total_cost_usd / run.max_cost_usd) * 100).toFixed(1);
      lines.push(`**Budget used:** ${pct}% of ${formatCost(run.max_cost_usd)} limit`);
    }
    lines.push("");
    lines.push("Cost per step (highest first):");
    for (const s of sorted) {
      const pct = run.total_cost_usd > 0 ? ((s.cost_usd / run.total_cost_usd) * 100).toFixed(0) : "0";
      lines.push(`- **${s.step_id}**: ${formatCost(s.cost_usd)} (${pct}%)`);
    }
    if (sorted[0] && sorted[0].cost_usd > run.total_cost_usd * 0.5) {
      lines.push("");
      lines.push(`**${sorted[0].step_id}** accounts for over half the total cost.`);
    }
    return lines.join("\n");
  }

  if (/slow|duration|long|time|fast/.test(q)) {
    if (steps.length === 0) {
      return "No step-level timing data available.";
    }
    const sorted = [...steps].sort(
      (a, b) => b.duration_seconds - a.duration_seconds
    );
    const totalDur = steps.reduce((sum, s) => sum + s.duration_seconds, 0);
    const avgDur = totalDur / steps.length;
    const lines: string[] = [];
    lines.push(`**Total step time:** ${formatDuration(totalDur)}`);
    lines.push(`**Average per step:** ${formatDuration(avgDur)}`);
    lines.push("");
    lines.push("Steps by duration (slowest first):");
    for (const s of sorted) {
      const marker = s.duration_seconds > avgDur * 2 ? " (slow)" : "";
      lines.push(
        `- **${s.step_id}**: ${formatDuration(s.duration_seconds)}${marker}`
      );
    }
    if (sorted[0] && sorted[0].duration_seconds > avgDur * 3) {
      lines.push("");
      lines.push(
        `**${sorted[0].step_id}** is significantly slower than average. Consider optimizing this step.`
      );
    }
    return lines.join("\n");
  }

  if (/fix|solve|help|resolve|how/.test(q)) {
    const lines: string[] = [];
    if (run.status !== "failed" && failedSteps.length === 0) {
      lines.push("This run completed successfully. No fixes needed.");
      return lines.join("\n");
    }
    const errors = [
      ...(run.error ? [run.error] : []),
      ...failedSteps.map((s) => s.error).filter(Boolean),
    ];
    const combined = errors.join(" ").toLowerCase();

    lines.push("**Suggested fixes:**");
    lines.push("");

    if (/timeout|timed out|deadline/.test(combined)) {
      lines.push("- Increase the timeout limit for the failing step");
      lines.push("- Check if an external service is responding slowly");
      lines.push("- Consider breaking the step into smaller units");
    } else if (/auth|unauthorized|403|401|forbidden/.test(combined)) {
      lines.push("- Verify your API keys are correct and not expired");
      lines.push("- Check that credentials have the required permissions");
      lines.push("- Ensure the integration is properly configured");
    } else if (/rate.?limit|429|too many/.test(combined)) {
      lines.push("- Add delays between API calls");
      lines.push("- Reduce parallel execution count");
      lines.push("- Check your API plan limits");
    } else if (/not.?found|404|missing/.test(combined)) {
      lines.push("- Verify the resource or endpoint still exists");
      lines.push("- Check for typos in step configuration");
      lines.push("- Ensure input data references valid resources");
    } else if (/memory|oom|out of memory/.test(combined)) {
      lines.push("- Reduce input data size");
      lines.push("- Process data in smaller batches");
      lines.push("- Increase the sandbox memory limit");
    } else {
      lines.push("- Review the error messages in the failed steps above");
      lines.push("- Check the workflow configuration for this step");
      lines.push("- Try replaying from the failed step");
      lines.push("- Check the integration/connector logs");
    }

    if (failedSteps.length > 0) {
      const retried = failedSteps.filter((s) => s.attempt > 1);
      if (retried.length > 0) {
        lines.push("");
        lines.push(
          `Note: ${retried.length} step(s) were already retried (up to attempt ${Math.max(...retried.map((s) => s.attempt))}).`
        );
      }
    }
    return lines.join("\n");
  }

  if (/status|state|current|now|happening/.test(q)) {
    if (run.status === "running") {
      const running = steps.filter((s) => s.status === "running");
      const done = completedSteps.length;
      const lines: string[] = [];
      lines.push(`**Run is in progress.** ${done}/${steps.length} steps completed.`);
      if (running.length > 0) {
        lines.push(`Currently executing: ${running.map((s) => `**${s.step_id}**`).join(", ")}`);
      }
      return lines.join("\n\n");
    }
    return `This run has status: **${run.status}**.`;
  }

  return [
    "I can help you understand this run. Try asking:",
    "",
    "- **Why did this fail?** - Error analysis",
    "- **Summarize this run** - Overview of all steps",
    "- **Cost breakdown** - Cost per step",
    "- **Which step was slowest?** - Duration analysis",
    "- **How can I fix this?** - Suggested fixes",
  ].join("\n");
}

function getSuggestedQuestions(status: string): string[] {
  if (status === "failed") {
    return [
      "Why did this fail?",
      "How can I fix this?",
      "Summarize this run",
    ];
  }
  if (status === "running" || status === "queued") {
    return [
      "What's happening now?",
      "Summarize this run",
      "Cost breakdown",
    ];
  }
  return [
    "Summarize this run",
    "Cost breakdown",
    "Which step was slowest?",
  ];
}

function formatMessageTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  return `${Math.floor(diffSec / 3600)}h ago`;
}

function renderFormattedText(text: string) {
  const lines = text.split("\n");
  return lines.map((line, i) => {
    const parts = line.split(/(\*\*[^*]+\*\*)/g).map((part, j) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return (
          <span key={j} className="font-semibold">
            {part.slice(2, -2)}
          </span>
        );
      }
      return <span key={j}>{part}</span>;
    });
    return (
      <span key={i}>
        {parts}
        {i < lines.length - 1 && <br />}
      </span>
    );
  });
}

interface AiChatSidebarProps {
  open: boolean;
  onClose: () => void;
  run: RunData;
}

export function AiChatSidebar({ open, onClose, run }: AiChatSidebarProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [typing, setTyping] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open && inputRef.current) {
      setTimeout(() => inputRef.current?.focus(), 300);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, typing]);

  const sendMessage = useCallback(
    (text: string) => {
      if (!text.trim() || typing) return;
      const question = text.trim();
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        text: question,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setTyping(true);

      void (async () => {
        // Ask the advisor-backed Run Assistant; fall back to the local
        // heuristics when no provider is configured or the call fails.
        let response: string;
        try {
          const history = messages.slice(-10).map((m) => ({
            role: m.role,
            text: m.text,
          }));
          const res = await api.post<{ answer: string }>(
            `/runs/${run.run_id}/assistant`,
            { question, history }
          );
          if (res.error || !res.data?.answer) throw new Error(res.error?.message);
          response = res.data.answer;
        } catch {
          response = generateResponse(question, run);
        }
        const assistantMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          text: response,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, assistantMsg]);
        setTyping(false);
      })();
    },
    [run, typing, messages]
  );

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      sendMessage(input);
    },
    [input, sendMessage]
  );

  const suggested = getSuggestedQuestions(run.status);

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/20 sm:hidden"
          onClick={onClose}
        />
      )}

      <div
        className={cn(
          "fixed top-0 right-0 z-50 h-full w-full sm:w-80 border-l border-border bg-surface",
          "flex flex-col shadow-xl",
          "transition-transform duration-300 ease-in-out",
          open ? "translate-x-0" : "translate-x-full"
        )}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <span className="text-sm font-semibold text-foreground">
              Run Assistant
            </span>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-muted hover:text-foreground hover:bg-border/40 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
          {messages.length === 0 && !typing && (
            <div className="space-y-3 pt-4">
              <p className="text-xs text-muted text-center">
                Ask me anything about this run
              </p>
              <div className="flex flex-wrap justify-center gap-1.5">
                {suggested.map((q) => (
                  <button
                    key={q}
                    onClick={() => sendMessage(q)}
                    className={cn(
                      "rounded-full border border-border px-3 py-1.5 text-xs text-muted",
                      "hover:bg-accent/10 hover:text-foreground hover:border-accent/30",
                      "transition-colors"
                    )}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={cn(
                "flex flex-col max-w-[90%]",
                msg.role === "user" ? "ml-auto items-end" : "mr-auto items-start"
              )}
            >
              <div
                className={cn(
                  "rounded-lg px-3 py-2 text-xs leading-relaxed",
                  msg.role === "user"
                    ? "bg-accent/10 text-foreground"
                    : "bg-background border border-border text-foreground"
                )}
              >
                {msg.role === "assistant"
                  ? renderFormattedText(msg.text)
                  : msg.text}
              </div>
              <span className="mt-0.5 text-[10px] text-muted-foreground px-1">
                {formatMessageTime(msg.timestamp)}
              </span>
            </div>
          ))}

          {typing && (
            <div className="mr-auto max-w-[90%]">
              <div className="rounded-lg bg-background border border-border px-3 py-2">
                <div className="flex items-center gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <form
          onSubmit={handleSubmit}
          className="border-t border-border px-3 py-2.5 flex items-center gap-2"
        >
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about this run..."
            disabled={typing}
            className={cn(
              "flex-1 h-8 rounded-lg border border-border bg-background px-3 text-xs text-foreground",
              "placeholder:text-muted-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30",
              "transition-colors disabled:opacity-50"
            )}
          />
          <button
            type="submit"
            disabled={!input.trim() || typing}
            className={cn(
              "flex items-center justify-center h-8 w-8 rounded-lg",
              "bg-accent text-accent-foreground",
              "hover:bg-accent/90 transition-colors",
              "disabled:opacity-60 disabled:cursor-not-allowed"
            )}
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </form>
      </div>
    </>
  );
}
