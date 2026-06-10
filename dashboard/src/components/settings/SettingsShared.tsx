import { useState } from "react";
import { Loader2, ChevronDown, Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";
import type { BackendOption } from "./settingsHelpers";

// -- SaveButton -------------------------------------------------------------

export function SaveButton({
  dirty,
  saving,
  onClick,
}: {
  dirty: boolean;
  saving: boolean;
  onClick: () => void;
}) {
  return (
    <button
      disabled={!dirty || saving}
      onClick={onClick}
      className={cn(
        "flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
        dirty
          ? "bg-accent text-accent-foreground hover:bg-accent-hover shadow-sm hover:shadow-md cursor-pointer"
          : "bg-border text-muted cursor-not-allowed",
      )}
    >
      {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      Save
    </button>
  );
}

// -- BackendCard ------------------------------------------------------------

export function BackendCard({
  icon: Icon,
  label,
  current,
  options,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  current: string;
  options: BackendOption[];
}) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const handleCopy = (text: string, optId: string) => {
    void navigator.clipboard?.writeText(text).catch(() => {/* clipboard unavailable */});
    setCopied(optId);
    setTimeout(() => setCopied(null), 2000);
  };

  return (
    <div className="rounded-lg border border-border/70 bg-surface/50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 px-4 py-3 cursor-pointer"
      >
        <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
        <span className="text-sm font-medium text-foreground">{label}</span>
        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-accent/15 border border-accent/30 text-accent capitalize">
          {current}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-3.5 w-3.5 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-border/50">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {options.map((opt) => {
              const isActive = opt.id === current;
              return (
                <div
                  key={opt.id}
                  className={cn(
                    "rounded-lg border p-3 transition-all",
                    isActive
                      ? "border-accent bg-accent/5"
                      : "border-border/50 bg-surface/30",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{opt.label}</span>
                    {isActive && (
                      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold bg-accent/20 text-accent uppercase tracking-wider">
                        Active
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-muted-foreground mt-0.5">{opt.desc}</p>
                  {!isActive && (
                    <div className="mt-2">
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-muted-foreground">Add to .env:</span>
                        <button
                          onClick={() => handleCopy(opt.envHint, opt.id)}
                          className="flex items-center gap-1 text-[10px] text-accent hover:text-accent-hover transition-colors cursor-pointer"
                        >
                          {copied === opt.id ? (
                            <><Check className="h-2.5 w-2.5" /> Copied</>
                          ) : (
                            <><Copy className="h-2.5 w-2.5" /> Copy</>
                          )}
                        </button>
                      </div>
                      <pre className="text-[10px] font-mono text-muted-foreground/70 bg-border/20 rounded px-2 py-1.5 whitespace-pre-wrap break-all">
                        {opt.envHint}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[10px] text-muted-foreground/60 mt-2">
            Changes require restarting Sandcastle to take effect.
          </p>
        </div>
      )}
    </div>
  );
}
