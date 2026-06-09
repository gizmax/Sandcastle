import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Castle, Check, CheckCircle, Loader2, Rocket } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

const CHECKLIST_KEY = "sandcastle_getting_started";

interface ChecklistState {
  dismissed: boolean;
  completed: Record<string, boolean>;
}

function getChecklistState(): ChecklistState {
  try {
    const raw = localStorage.getItem(CHECKLIST_KEY);
    return raw ? JSON.parse(raw) : { dismissed: false, completed: {} };
  } catch {
    return { dismissed: false, completed: {} };
  }
}

function saveChecklistState(state: ChecklistState) {
  localStorage.setItem(CHECKLIST_KEY, JSON.stringify(state));
}

interface DemoRunResult {
  run_id: string;
  status: string;
  outputs: { generate: string; summarize: string };
  total_cost_usd: number;
}

export function BentoEmptyState() {
  const navigate = useNavigate();
  const [cls, setCls] = useState<ChecklistState>(getChecklistState);

  const [demoState, setDemoState] = useState<"idle" | "step1" | "step2" | "done" | "error">("idle");
  const [demoOutput, setDemoOutput] = useState<string | null>(null);

  const missions = [
    {
      id: "first-run",
      title: "Run your first workflow",
      desc: "One click. See real output in seconds.",
      time: "2 min",
      action: () => navigate("/templates?tab=packs"),
      cta: "Pick a template",
    },
    {
      id: "api-key",
      title: "Connect an AI provider",
      desc: "Paste your API key. Test the connection.",
      time: "1 min",
      action: () => navigate("/settings"),
      cta: "Open Settings",
    },
    {
      id: "build-pipeline",
      title: "Build a multi-step pipeline",
      desc: "Chain steps together: scrape, analyze, summarize.",
      time: "5 min",
      action: () => navigate("/workflows/builder"),
      cta: "Open Builder",
    },
  ];

  const quickRuns = [
    {
      id: "summarize",
      icon: "📝",
      title: "Summarize text",
      desc: "Paste any text, get a concise summary",
      template: "text-summarizer",
    },
    {
      id: "analyze-url",
      icon: "🔍",
      title: "Analyze a URL",
      desc: "Enter a URL, get structured analysis",
      template: "url-analyzer",
    },
    {
      id: "generate-code",
      icon: "💻",
      title: "Generate code",
      desc: "Describe what you need, get working code",
      template: "code-generator",
    },
    {
      id: "research",
      icon: "📊",
      title: "Research a topic",
      desc: "Enter a topic, get a structured report",
      template: "research-report",
    },
  ];

  const completedCount = Object.values(cls.completed).filter(Boolean).length;
  const allDone = completedCount >= missions.length;

  const toggleMission = (id: string) => {
    const next = {
      ...cls,
      completed: { ...cls.completed, [id]: !cls.completed[id] },
    };
    setCls(next);
    saveChecklistState(next);
  };

  const dismissChecklist = () => {
    const next = { ...cls, dismissed: true };
    setCls(next);
    saveChecklistState(next);
  };

  const runDemo = async () => {
    setDemoState("step1");
    setDemoOutput(null);
    try {
      await new Promise((r) => setTimeout(r, 1000));
      setDemoState("step2");

      const res = await api.post<DemoRunResult>("/workflows/run", {
        workflow_name: "demo-hello-world",
        workflow: {
          name: "demo-hello-world",
          description: "Your first Sandcastle workflow",
          default_model: "sonnet",
          steps: [
            { id: "generate", prompt: "Write 3 interesting facts about sandcastles." },
            { id: "summarize", prompt: "Summarize these facts in one sentence: {steps.generate.output}", depends_on: ["generate"] },
          ],
        },
      });

      if (res.data) {
        setDemoOutput(res.data.outputs?.summarize || "Workflow completed successfully.");
        setDemoState("done");
        const next = { ...cls, completed: { ...cls.completed, "first-run": true } };
        setCls(next);
        saveChecklistState(next);
      } else {
        setDemoState("error");
      }
    } catch {
      setDemoState("error");
    }
  };

  return (
    <div className="space-y-4">
      {!cls.dismissed && (
        <div className="bg-surface rounded-2xl shadow-sm border border-border p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent/10">
                <Castle className="h-5 w-5 text-accent" />
              </div>
              <div>
                <h2 className="text-lg font-bold text-foreground tracking-tight">
                  Getting Started
                </h2>
                <p className="text-xs text-muted-foreground">
                  {allDone ? "All done! You're ready to build." : `${completedCount}/${missions.length} completed`}
                </p>
              </div>
            </div>
            <button
              onClick={dismissChecklist}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Dismiss
            </button>
          </div>

          <div className="space-y-2">
            {missions.map((m) => {
              const done = cls.completed[m.id];
              return (
                <div
                  key={m.id}
                  className={cn(
                    "flex items-center gap-3 rounded-xl border p-3 transition-all",
                    done
                      ? "border-success/30 bg-success/5"
                      : "border-border hover:border-accent/30",
                  )}
                >
                  <button
                    onClick={() => toggleMission(m.id)}
                    className={cn(
                      "flex items-center justify-center w-6 h-6 rounded-full border-2 shrink-0 transition-colors cursor-pointer",
                      done
                        ? "bg-success border-success text-white"
                        : "border-border hover:border-accent",
                    )}
                  >
                    {done && <Check className="h-3.5 w-3.5" />}
                  </button>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        "text-sm font-medium",
                        done ? "text-muted-foreground line-through" : "text-foreground",
                      )}>
                        {m.title}
                      </span>
                      <span className="text-[10px] text-muted-foreground/60">{m.time}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{m.desc}</p>
                  </div>
                  {!done && (
                    <button
                      onClick={m.action}
                      className="shrink-0 text-xs font-medium text-accent hover:text-accent-hover transition-colors cursor-pointer"
                    >
                      {m.cta} &rarr;
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className={cn(
        "bg-surface rounded-2xl shadow-sm border p-6",
        demoState === "done" ? "border-success/30" : "border-border",
      )}>
        <div className="flex items-start gap-4">
          <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-accent/10 shrink-0">
            <Rocket className="h-5 w-5 text-accent" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-lg font-bold text-foreground tracking-tight">
              Run Your First Workflow
            </h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              See Sandcastle in action. A 2-step demo that summarizes text.
            </p>

            <div className="mt-4">
              {demoState === "idle" && (
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => void runDemo()}
                    className={cn(
                      "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
                      "hover:bg-accent-hover transition-all shadow-sm hover:shadow-md active:scale-[0.98] cursor-pointer",
                    )}
                  >
                    Run Demo
                    <ArrowRight className="h-4 w-4" />
                  </button>
                  <span className="text-xs text-muted-foreground">
                    Free with Ollama &middot; ~$0.01 with Claude
                  </span>
                </div>
              )}

              {demoState === "step1" && (
                <div className="flex items-center gap-2.5">
                  <Loader2 className="h-4 w-4 text-accent animate-spin" />
                  <span className="text-sm text-foreground">Running step 1 of 2 - Generating facts...</span>
                </div>
              )}

              {demoState === "step2" && (
                <div className="flex items-center gap-2.5">
                  <Loader2 className="h-4 w-4 text-accent animate-spin" />
                  <span className="text-sm text-foreground">Running step 2 of 2 - Summarizing...</span>
                </div>
              )}

              {demoState === "done" && demoOutput && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="h-4 w-4 text-success" />
                    <span className="text-sm font-semibold text-success">Done!</span>
                  </div>
                  <div className="rounded-lg bg-accent/5 border border-accent/20 p-3">
                    <p className="text-sm text-foreground leading-relaxed">{demoOutput}</p>
                  </div>
                </div>
              )}

              {demoState === "error" && (
                <div className="space-y-1.5">
                  <p className="text-sm text-error">Something went wrong.</p>
                  <button
                    onClick={() => void runDemo()}
                    className="text-xs font-medium text-accent hover:text-accent-hover transition-colors cursor-pointer"
                  >
                    Retry
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="bg-surface rounded-2xl shadow-sm border border-border p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold text-foreground tracking-tight">Quick Start</h2>
            <p className="text-xs text-muted-foreground">
              Jump straight in. Each runs a pre-built workflow.
            </p>
          </div>
          <button
            onClick={() => navigate("/templates")}
            className="text-xs font-medium text-accent hover:text-accent-hover transition-colors"
          >
            Browse all templates &rarr;
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {quickRuns.map((qr) => (
            <button
              key={qr.id}
              onClick={() => navigate(`/templates?search=${qr.template}`)}
              className={cn(
                "text-left rounded-xl border border-border p-4",
                "hover:border-accent/40 hover:bg-accent/5",
                "transition-settle active:scale-[0.98] cursor-pointer group",
              )}
            >
              <span className="text-2xl">{qr.icon}</span>
              <h3 className="text-sm font-semibold text-foreground mt-2 group-hover:text-accent transition-colors">
                {qr.title}
              </h3>
              <p className="text-xs text-muted-foreground mt-1">{qr.desc}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
