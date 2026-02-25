import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Loader2, CheckCircle, XCircle } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { API_BASE_URL } from "@/lib/constants";

interface StepFirstRunProps {
  selectedTemplate: string | null;
  onNext: () => void;
  onBack: () => void;
}

type RunStatus = "idle" | "starting" | "running" | "completed" | "failed";

interface LogLine {
  time: string;
  text: string;
  type: "info" | "success" | "error" | "step";
}

function timestamp(): string {
  return new Date().toLocaleTimeString("en-US", { hour12: false });
}

/**
 * First run step - auto-triggers a workflow run and shows live progress.
 * Uses SSE for real-time updates when available, falls back to polling / demo progress.
 */
export function StepFirstRun({ selectedTemplate, onNext, onBack }: StepFirstRunProps) {
  const [status, setStatus] = useState<RunStatus>("idle");
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const hasStarted = useRef(false);

  function addLog(text: string, type: LogLine["type"] = "info") {
    setLogs((prev) => [...prev, { time: timestamp(), text, type }]);
  }

  // Auto-scroll logs to bottom
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  // Auto-start run on mount
  useEffect(() => {
    if (hasStarted.current) return;
    hasStarted.current = true;
    void startRun();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startRun() {
    setStatus("starting");
    addLog("Initializing workflow run...");

    if (!selectedTemplate) {
      addLog("No template selected - running demo workflow.", "info");
      await runDemoProgress();
      return;
    }

    addLog(`Template: ${selectedTemplate}`);

    try {
      // Try to start a real run
      const res = await api.post<{ id: string; status: string }>("/runs", {
        workflow: selectedTemplate,
        inputs: {},
      });

      if (res.error || !res.data?.id) {
        addLog("Backend unavailable - running demo simulation.", "info");
        await runDemoProgress();
        return;
      }

      const id = res.data.id;
      setRunId(id);
      addLog(`Run started: ${id.slice(0, 12)}...`, "step");
      setStatus("running");

      // Try SSE streaming for live updates
      await streamRunEvents(id);
    } catch {
      addLog("Could not reach backend - simulating run.", "info");
      await runDemoProgress();
    }
  }

  async function streamRunEvents(id: string) {
    try {
      const url = `${API_BASE_URL}/runs/${id}/events`;
      const response = await fetch(url, {
        headers: api.authHeaders(),
      });

      if (!response.ok || !response.body) {
        // Fallback to polling
        await pollRunStatus(id);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const event = JSON.parse(line.slice(6));
              handleSSEEvent(event);
            } catch {
              // Skip malformed events
            }
          }
        }
      }

      // If stream ends and we haven't completed, check final status
      if (status === "running") {
        await checkFinalStatus(id);
      }
    } catch {
      // SSE failed, try polling
      await pollRunStatus(id);
    }
  }

  function handleSSEEvent(event: { type: string; data?: Record<string, unknown> }) {
    const stepName = (event.data?.step_name as string) ?? "";
    switch (event.type) {
      case "step.started":
        addLog(`Step '${stepName}' started...`, "step");
        break;
      case "step.completed":
        addLog(`Step '${stepName}' completed.`, "success");
        break;
      case "step.failed":
        addLog(`Step '${stepName}' failed.`, "error");
        break;
      case "run.completed":
        addLog("Workflow completed successfully!", "success");
        setStatus("completed");
        break;
      case "run.failed":
        addLog("Workflow run failed.", "error");
        setStatus("failed");
        break;
    }
  }

  async function pollRunStatus(id: string) {
    const maxPolls = 30;
    for (let i = 0; i < maxPolls; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const res = await api.get<{ status: string }>(`/runs/${id}`);
        const s = res.data?.status;
        if (s === "completed") {
          addLog("Workflow completed successfully!", "success");
          setStatus("completed");
          return;
        } else if (s === "failed" || s === "cancelled") {
          addLog(`Workflow ${s}.`, "error");
          setStatus("failed");
          return;
        }
        if (i % 3 === 0) {
          addLog("Processing...", "info");
        }
      } catch {
        break;
      }
    }
    // If polling times out, consider it completed for onboarding purposes
    addLog("Run timed out - you can check status in the dashboard.", "info");
    setStatus("completed");
  }

  async function checkFinalStatus(id: string) {
    try {
      const res = await api.get<{ status: string }>(`/runs/${id}`);
      if (res.data?.status === "completed") {
        addLog("Workflow completed!", "success");
        setStatus("completed");
      } else if (res.data?.status === "failed") {
        addLog("Workflow failed.", "error");
        setStatus("failed");
      }
    } catch {
      setStatus("completed");
    }
  }

  async function runDemoProgress() {
    setStatus("running");
    const steps = [
      { text: "Preparing sandbox environment...", delay: 800 },
      { text: "Step 'fetch-data' started...", delay: 600, type: "step" as const },
      { text: "Step 'fetch-data' completed.", delay: 1200, type: "success" as const },
      { text: "Step 'analyze' started...", delay: 400, type: "step" as const },
      { text: "Processing input data...", delay: 1000 },
      { text: "Step 'analyze' completed.", delay: 800, type: "success" as const },
      { text: "Step 'generate-report' started...", delay: 500, type: "step" as const },
      { text: "Generating output...", delay: 1000 },
      { text: "Step 'generate-report' completed.", delay: 600, type: "success" as const },
      { text: "Workflow completed successfully!", delay: 400, type: "success" as const },
    ];

    for (const step of steps) {
      await new Promise((r) => setTimeout(r, step.delay));
      addLog(step.text, step.type ?? "info");
    }
    setStatus("completed");
  }

  const isFinished = status === "completed" || status === "failed";

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">First Run</h2>
        <p className="mt-1 text-sm text-muted">
          {isFinished
            ? "Your workflow run is complete."
            : "Running your first workflow..."}
        </p>
      </div>

      {/* Status indicator */}
      <div className="flex items-center justify-center gap-2">
        {(status === "starting" || status === "running") && (
          <Loader2 className="h-4 w-4 animate-spin text-accent" />
        )}
        {status === "completed" && (
          <CheckCircle className="h-4 w-4 text-success" />
        )}
        {status === "failed" && (
          <XCircle className="h-4 w-4 text-error" />
        )}
        <span
          className={cn(
            "text-sm font-medium",
            status === "completed" && "text-success",
            status === "failed" && "text-error",
            (status === "starting" || status === "running") && "text-accent"
          )}
        >
          {status === "idle" && "Preparing..."}
          {status === "starting" && "Starting..."}
          {status === "running" && "Running..."}
          {status === "completed" && "Completed"}
          {status === "failed" && "Failed"}
        </span>
        {runId && (
          <span className="text-xs text-muted font-mono">
            ({runId.slice(0, 8)})
          </span>
        )}
      </div>

      {/* Terminal-style log display */}
      <div
        ref={logRef}
        className="h-56 overflow-y-auto rounded-xl border border-border bg-[#0A0A0A] p-4 font-mono text-xs leading-relaxed"
      >
        {logs.map((log, i) => (
          <div key={i} className="flex gap-2">
            <span className="shrink-0 text-[#555]">{log.time}</span>
            <span
              className={cn(
                log.type === "success" && "text-[#22C55E]",
                log.type === "error" && "text-[#EF4444]",
                log.type === "step" && "text-[#FBBF24]",
                log.type === "info" && "text-[#A3A3A3]"
              )}
            >
              {log.text}
            </span>
          </div>
        ))}
        {(status === "starting" || status === "running") && (
          <div className="flex gap-2">
            <span className="shrink-0 text-[#555]">{timestamp()}</span>
            <span className="text-[#A3A3A3] animate-pulse">_</span>
          </div>
        )}
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between pt-2">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>
        <button
          onClick={onNext}
          disabled={!isFinished}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm",
            "disabled:opacity-40 disabled:cursor-not-allowed"
          )}
        >
          {isFinished ? "Continue" : "Running..."}
          <ArrowRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
