import { useState } from "react";
import { CheckCircle, Loader2, Server, Zap } from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { useRuntimeInfo } from "@/hooks/useRuntimeInfo";

interface StepConnectSandshoreProps {
  onComplete: () => void;
}

export function StepConnectSandshore({ onComplete }: StepConnectSandshoreProps) {
  const [testing, setTesting] = useState(false);
  const [connected, setConnected] = useState(false);
  const { info } = useRuntimeInfo();

  async function handleTest() {
    setTesting(true);
    try {
      const res = await api.get<{ status: string; sandstorm: boolean }>("/health");
      if (res.data?.sandstorm) {
        setConnected(true);
      }
    } catch {
      // Not connected
    }
    setTesting(false);
  }

  return (
    <div className="space-y-6 text-center">
      <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-accent/10">
        <Server className="h-8 w-8 text-accent" />
      </div>
      <div>
        <h2 className="text-lg font-semibold text-foreground">Connect to Sandshore</h2>
        <p className="mt-1 text-sm text-muted">
          Verify that your Sandshore runtime is running and accessible.
        </p>
      </div>

      {info && (
        <div className="mx-auto max-w-xs rounded-lg border border-border/50 bg-surface-secondary/50 p-3">
          <div className="flex items-center justify-center gap-2 text-xs font-medium text-muted">
            <Zap className="h-3.5 w-3.5" />
            <span>
              {info.mode === "local" ? "Local mode" : "Production mode"} detected
            </span>
          </div>
          <div className="mt-1.5 flex items-center justify-center gap-3 text-xs text-muted/70">
            <span>{info.database}</span>
            <span className="text-border">|</span>
            <span>{info.queue}</span>
            <span className="text-border">|</span>
            <span>{info.storage}</span>
            <span className="text-border">|</span>
            <span className="capitalize">{info.sandbox_backend}</span>
          </div>
        </div>
      )}

      {connected ? (
        <div className="flex items-center justify-center gap-2 text-success">
          <CheckCircle className="h-5 w-5" />
          <span className="text-sm font-medium">Connected successfully</span>
        </div>
      ) : (
        <button
          onClick={handleTest}
          disabled={testing}
          className={cn(
            "rounded-lg bg-accent px-6 py-2.5 text-sm font-medium text-accent-foreground",
            "hover:bg-accent-hover transition-all duration-200 shadow-sm",
            "disabled:opacity-50"
          )}
        >
          {testing ? (
            <span className="flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Testing...
            </span>
          ) : (
            "Test Connection"
          )}
        </button>
      )}

      <div className="pt-4">
        <button
          onClick={onComplete}
          className="text-sm text-muted hover:text-foreground transition-colors"
        >
          {connected ? "Continue" : "Skip"}
        </button>
      </div>
    </div>
  );
}
