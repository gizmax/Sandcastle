import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  XCircle,
  Loader2,
  RefreshCw,
  Shield,
} from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

interface StepApiKeysCheckProps {
  onNext: () => void;
  onBack: () => void;
}

interface KeyStatus {
  name: string;
  label: string;
  configured: boolean;
  required: boolean;
}

/**
 * Step 4: API Keys check - read-only display of which keys are configured
 * vs missing. Queries GET /health (or /runtime) and shows status.
 */
export function StepApiKeysCheck({ onNext, onBack }: StepApiKeysCheckProps) {
  const [keys, setKeys] = useState<KeyStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [backendStatus, setBackendStatus] = useState<"ok" | "degraded" | "unavailable">("unavailable");

  async function checkKeys() {
    setLoading(true);
    try {
      const res = await api.get<{
        status: string;
        sandbox_backend?: string;
        configured_keys?: string[];
        missing_keys?: string[];
      }>("/health");

      if (res.data) {
        const status = res.data.status;
        setBackendStatus(status === "ok" ? "ok" : status === "degraded" ? "degraded" : "unavailable");

        // If the API returns configured/missing keys, use those
        const configured = new Set(res.data.configured_keys || []);
        const missing = res.data.missing_keys || [];

        const allKeys: KeyStatus[] = [];

        // Always show common keys
        const commonKeys = [
          { name: "ANTHROPIC_API_KEY", label: "Anthropic API Key", required: true },
          { name: "OPENAI_API_KEY", label: "OpenAI API Key", required: false },
        ];

        // Backend-specific key
        const backend = res.data.sandbox_backend;
        if (backend === "e2b") {
          commonKeys.push({ name: "E2B_API_KEY", label: "E2B API Key", required: true });
        } else if (backend === "cloudflare") {
          commonKeys.push({ name: "CF_ACCOUNT_ID", label: "Cloudflare Account ID", required: true });
          commonKeys.push({ name: "CF_API_TOKEN", label: "Cloudflare API Token", required: true });
        }

        for (const key of commonKeys) {
          allKeys.push({
            ...key,
            configured: configured.has(key.name) || !missing.includes(key.name),
          });
        }

        // Add any extra configured keys not in our common list
        for (const k of configured) {
          if (!commonKeys.find((c) => c.name === k)) {
            allKeys.push({
              name: k,
              label: k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
              configured: true,
              required: false,
            });
          }
        }

        setKeys(allKeys);
      } else {
        // Backend unavailable - show fallback
        setBackendStatus("unavailable");
        setKeys([
          { name: "ANTHROPIC_API_KEY", label: "Anthropic API Key", configured: false, required: true },
          { name: "E2B_API_KEY", label: "E2B API Key", configured: false, required: false },
          { name: "OPENAI_API_KEY", label: "OpenAI API Key", configured: false, required: false },
        ]);
      }
    } catch {
      setBackendStatus("unavailable");
      setKeys([
        { name: "ANTHROPIC_API_KEY", label: "Anthropic API Key", configured: false, required: true },
        { name: "E2B_API_KEY", label: "E2B API Key", configured: false, required: false },
        { name: "OPENAI_API_KEY", label: "OpenAI API Key", configured: false, required: false },
      ]);
    }
    setLoading(false);
  }

  useEffect(() => {
    void checkKeys();
  }, []);

  const configuredCount = keys.filter((k) => k.configured).length;
  const totalCount = keys.length;

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">API Keys Check</h2>
        <p className="mt-1 text-sm text-muted">
          Verify that the required API keys are configured in your environment.
        </p>
      </div>

      {/* Backend status banner */}
      <div
        className={cn(
          "flex items-center gap-3 rounded-lg border px-4 py-3 text-sm",
          backendStatus === "ok" && "border-success/30 bg-success/5 text-success",
          backendStatus === "degraded" && "border-warning/30 bg-warning/5 text-warning",
          backendStatus === "unavailable" && "border-border bg-background text-muted"
        )}
      >
        <Shield className="h-4 w-4 shrink-0" />
        <span>
          {backendStatus === "ok" && "Backend connected and healthy."}
          {backendStatus === "degraded" && "Backend connected but degraded."}
          {backendStatus === "unavailable" && "Backend unavailable - keys shown are defaults. Configure via .env file."}
        </span>
      </div>

      {/* Key status list */}
      {loading ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-accent" />
        </div>
      ) : (
        <div className="space-y-2 stagger-children">
          {keys.map((key) => (
            <div
              key={key.name}
              className={cn(
                "flex items-center justify-between rounded-lg border px-4 py-3 transition-colors",
                key.configured
                  ? "border-success/20 bg-success/[0.03]"
                  : key.required
                    ? "border-error/20 bg-error/[0.03]"
                    : "border-border bg-background"
              )}
            >
              <div className="flex items-center gap-3">
                {key.configured ? (
                  <CheckCircle className="h-4 w-4 text-success shrink-0" />
                ) : (
                  <XCircle className={cn("h-4 w-4 shrink-0", key.required ? "text-error" : "text-muted")} />
                )}
                <div>
                  <p className="text-sm font-medium text-foreground">{key.label}</p>
                  <p className="text-xs font-mono text-muted">{key.name}</p>
                </div>
              </div>
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-[10px] font-semibold",
                  key.configured
                    ? "bg-success/15 text-success"
                    : key.required
                      ? "bg-error/15 text-error"
                      : "bg-muted/15 text-muted"
                )}
              >
                {key.configured ? "Configured" : key.required ? "Missing" : "Optional"}
              </span>
            </div>
          ))}

          {/* Summary */}
          <p className="text-center text-xs text-muted pt-2">
            {configuredCount}/{totalCount} keys configured
          </p>
        </div>
      )}

      {/* Refresh button */}
      <div className="flex justify-center">
        <button
          onClick={() => void checkKeys()}
          disabled={loading}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground",
            "hover:bg-accent/5 transition-all duration-200",
            "disabled:opacity-40 disabled:cursor-not-allowed"
          )}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
          Re-check
        </button>
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
        <div className="flex items-center gap-3">
          <button
            onClick={onNext}
            className="text-xs text-muted hover:text-foreground transition-colors"
          >
            Skip for now
          </button>
          <button
            onClick={onNext}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg bg-accent px-5 py-2 text-sm font-medium text-accent-foreground",
              "hover:bg-accent-hover transition-all duration-200 shadow-sm"
            )}
          >
            Continue
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
