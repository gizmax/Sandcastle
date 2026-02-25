import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Eye,
  EyeOff,
  Loader2,
  CheckCircle,
  XCircle,
} from "lucide-react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import type { BackendChoice } from "./StepBackendSelect";

interface StepApiKeysProps {
  backend: BackendChoice | null;
  apiKeys: Record<string, string>;
  onKeysChange: (keys: Record<string, string>) => void;
  onNext: () => void;
  onBack: () => void;
}

interface KeyField {
  id: string;
  label: string;
  placeholder: string;
  hint: string;
  required?: boolean;
}

// Key fields vary by selected backend
function getKeyFields(backend: BackendChoice | null): KeyField[] {
  const common: KeyField[] = [
    {
      id: "ANTHROPIC_API_KEY",
      label: "Anthropic API Key",
      placeholder: "sk-ant-...",
      hint: "Required for Claude model steps.",
      required: true,
    },
  ];

  const backendSpecific: Record<BackendChoice, KeyField[]> = {
    e2b: [
      {
        id: "E2B_API_KEY",
        label: "E2B API Key",
        placeholder: "e2b_...",
        hint: "Required for cloud sandbox execution.",
        required: true,
      },
    ],
    docker: [],
    local: [],
    cloudflare: [
      {
        id: "CF_ACCOUNT_ID",
        label: "Cloudflare Account ID",
        placeholder: "abc123...",
        hint: "Your Cloudflare account identifier.",
        required: true,
      },
      {
        id: "CF_API_TOKEN",
        label: "Cloudflare API Token",
        placeholder: "...",
        hint: "Token with Workers permissions.",
        required: true,
      },
    ],
  };

  const extra = backend ? backendSpecific[backend] : [];

  return [...extra, ...common];
}

/**
 * API keys configuration step.
 */
export function StepApiKeys({
  backend,
  apiKeys,
  onKeysChange,
  onNext,
  onBack,
}: StepApiKeysProps) {
  const [visibleKeys, setVisibleKeys] = useState<Record<string, boolean>>({});
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"success" | "error" | null>(null);

  const fields = getKeyFields(backend);

  function handleChange(id: string, value: string) {
    onKeysChange({ ...apiKeys, [id]: value });
    // Reset test result when keys change
    setTestResult(null);
  }

  function toggleVisibility(id: string) {
    setVisibleKeys((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  async function handleTestConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.get<{ status: string; runtime: boolean }>("/health");
      if (res.data?.status === "ok" || res.data?.runtime) {
        setTestResult("success");
      } else {
        setTestResult("error");
      }
    } catch {
      setTestResult("error");
    }
    setTesting(false);
  }

  const hasAnyKey = Object.values(apiKeys).some((v) => v.trim().length > 0);

  return (
    <div className="space-y-6">
      <div className="text-center">
        <h2 className="text-lg font-semibold text-foreground">Configure API Keys</h2>
        <p className="mt-1 text-sm text-muted">
          Add the keys needed for your{" "}
          <span className="font-medium text-foreground capitalize">{backend ?? "selected"}</span>{" "}
          backend and AI providers.
        </p>
      </div>

      {/* Key fields */}
      <div className="space-y-4">
        {fields.map((field) => (
          <div key={field.id} className="space-y-1.5">
            <label className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              {field.label}
              {field.required && <span className="text-accent">*</span>}
            </label>
            <div className="relative">
              <input
                type={visibleKeys[field.id] ? "text" : "password"}
                value={apiKeys[field.id] || ""}
                onChange={(e) => handleChange(field.id, e.target.value)}
                placeholder={field.placeholder}
                className={cn(
                  "w-full rounded-lg border border-border bg-background px-3 py-2 pr-10 text-sm text-foreground font-mono",
                  "placeholder:text-muted/50",
                  "focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent",
                  "transition-all duration-200"
                )}
              />
              <button
                type="button"
                onClick={() => toggleVisibility(field.id)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-foreground transition-colors"
                tabIndex={-1}
              >
                {visibleKeys[field.id] ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
            <p className="text-xs text-muted">{field.hint}</p>
          </div>
        ))}
      </div>

      {/* Test connection */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleTestConnection}
          disabled={testing || !hasAnyKey}
          className={cn(
            "inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm font-medium text-foreground",
            "hover:bg-accent/5 transition-all duration-200",
            "disabled:opacity-40 disabled:cursor-not-allowed"
          )}
        >
          {testing ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Testing...
            </>
          ) : (
            "Test Connection"
          )}
        </button>

        {testResult === "success" && (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-success">
            <CheckCircle className="h-4 w-4" />
            Connected
          </span>
        )}
        {testResult === "error" && (
          <span className="inline-flex items-center gap-1.5 text-sm text-error">
            <XCircle className="h-4 w-4" />
            Connection failed
          </span>
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
