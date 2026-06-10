import { useCallback, useEffect, useState } from "react";
import {
  Cpu,
  Loader2,
  CheckCircle2,
  X,
  Wifi,
} from "lucide-react";
import { toast } from "sonner";
import { Link } from "react-router-dom";
import { api } from "@/api/client";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { SectionCard, FieldLabel, HelperText } from "@/components/ui/SectionCard";
import { cn, inputClass } from "@/lib/utils";
import { SaveButton } from "./SettingsShared";
import { useSettingsContext } from "./settingsContext";

const REGION_LABEL: Record<string, string> = {
  us: "US",
  eu: "EU",
  local: "Local",
};

const PROVIDER_STATUS_LABEL: Record<string, string> = {
  ok: "Configured",
  unconfigured: "Not configured",
  running: "Running",
  not_detected: "Not detected",
};

/**
 * Providers tab — model providers, their status/region, EU data-residency and
 * the quality-routing override. This is the LLM-provider surface that used to
 * live at `/providers` and inside the old SettingsPage "AI Provider" section.
 */
export default function ProvidersPanel() {
  const { advisorStatus, refreshAdvisorStatus } = useSettingsContext();

  const [selectedProvider, setSelectedProvider] = useState<string>("");
  const [advisorModel, setAdvisorModel] = useState<string>("");
  const [euMode, setEuMode] = useState<boolean>(false);
  const [savingAdvisor, setSavingAdvisor] = useState(false);

  // Sync local form state when the shared advisor status (re)loads.
  useEffect(() => {
    if (advisorStatus) {
      setSelectedProvider(advisorStatus.current_provider);
      setAdvisorModel(advisorStatus.current_model || "");
      setEuMode(advisorStatus.data_residency === "eu");
    }
  }, [advisorStatus]);

  const handleSaveAdvisor = useCallback(async () => {
    setSavingAdvisor(true);
    const res = await api.post("/advisor/configure", {
      provider: selectedProvider,
      model: advisorModel || null,
      data_residency: euMode ? "eu" : null,
    });
    setSavingAdvisor(false);
    if (res.error) {
      toast.error(`Failed to save: ${res.error.message}`);
    } else {
      toast.success("AI Provider saved");
      refreshAdvisorStatus();
    }
  }, [selectedProvider, advisorModel, euMode, refreshAdvisorStatus]);

  // -- Connection test ------------------------------------------------------

  type TestState = "idle" | "testing" | "ok" | "error";
  const [testStates, setTestStates] = useState<Record<string, TestState>>({});
  const [testMessages, setTestMessages] = useState<Record<string, string>>({});

  const handleTestConnection = useCallback(async (providerId: string) => {
    setTestStates((prev) => ({ ...prev, [providerId]: "testing" }));
    setTestMessages((prev) => ({ ...prev, [providerId]: "" }));
    const res = await api.post<{ status: string; latency_ms?: number; message?: string }>(
      "/advisor/test-connection",
      { provider: providerId },
    );
    if (res.error) {
      setTestStates((prev) => ({ ...prev, [providerId]: "error" }));
      setTestMessages((prev) => ({ ...prev, [providerId]: res.error!.message }));
    } else if (res.data?.status === "ok") {
      setTestStates((prev) => ({ ...prev, [providerId]: "ok" }));
      setTestMessages((prev) => ({ ...prev, [providerId]: `${res.data!.latency_ms ?? "?"}ms` }));
      setTimeout(() => setTestStates((prev) => ({ ...prev, [providerId]: "idle" })), 3000);
    } else {
      setTestStates((prev) => ({ ...prev, [providerId]: "error" }));
      setTestMessages((prev) => ({ ...prev, [providerId]: res.data?.message ?? "Connection failed" }));
    }
  }, []);

  const advisorDirty =
    advisorStatus !== null &&
    (selectedProvider !== advisorStatus.current_provider ||
      (advisorModel || "") !== (advisorStatus.current_model || "") ||
      euMode !== (advisorStatus.data_residency === "eu"));

  return (
    <SectionCard
      icon={Cpu}
      title="AI Provider"
      description="Choose which LLM powers workflow generation, evolution, and quality evaluation."
    >
      <div className="space-y-4">
        {advisorStatus === null ? (
          <LoadingSpinner size="sm" />
        ) : (
          <>
            {/* Provider cards */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {advisorStatus.available_providers.map((p) => {
                const ts = testStates[p.id] ?? "idle";
                const tm = testMessages[p.id] ?? "";
                return (
                  <div
                    key={p.id}
                    className={cn(
                      "rounded-lg border p-4 text-center transition-all",
                      selectedProvider === p.id
                        ? "border-accent bg-accent/10"
                        : "border-border hover:border-accent/50",
                    )}
                  >
                    <button
                      className="w-full cursor-pointer"
                      onClick={() => setSelectedProvider(p.id)}
                    >
                      <div className="text-sm font-semibold text-foreground">{p.name}</div>
                      <div className="text-xs text-muted mt-0.5">{REGION_LABEL[p.region] ?? p.region}</div>
                      <div
                        className={cn(
                          "text-xs mt-1.5 font-medium",
                          p.status === "ok" || p.status === "running"
                            ? "text-success"
                            : "text-muted-foreground",
                        )}
                      >
                        {PROVIDER_STATUS_LABEL[p.status] ?? p.status}
                      </div>
                    </button>
                    {p.configured ? (
                      <div className="mt-1 flex items-center justify-center gap-1 text-[10px] text-success">
                        <CheckCircle2 className="h-2.5 w-2.5" />
                        Key set
                      </div>
                    ) : (
                      <Link
                        to="/settings?tab=keys"
                        className="mt-1 flex items-center justify-center gap-1 text-[10px] text-accent hover:text-accent-hover transition-colors"
                      >
                        Set API key &rarr;
                      </Link>
                    )}
                    <button
                      onClick={(e) => { e.stopPropagation(); void handleTestConnection(p.id); }}
                      disabled={ts === "testing"}
                      className={cn(
                        "mt-2 flex w-full items-center justify-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium transition-colors cursor-pointer",
                        ts === "ok" ? "bg-success/15 text-success" :
                        ts === "error" ? "bg-error/15 text-error" :
                        "bg-border/60 text-muted-foreground hover:bg-border",
                      )}
                      title={tm || "Test connection"}
                    >
                      {ts === "testing" && <Loader2 className="h-2.5 w-2.5 animate-spin" />}
                      {ts === "ok" && <CheckCircle2 className="h-2.5 w-2.5" />}
                      {ts === "error" && <X className="h-2.5 w-2.5" />}
                      {ts === "idle" && <Wifi className="h-2.5 w-2.5" />}
                      {ts === "ok" ? tm : ts === "error" ? "Failed" : ts === "testing" ? "Testing..." : "Test"}
                    </button>
                  </div>
                );
              })}
            </div>

            {/* Model override */}
            <div>
              <FieldLabel htmlFor="advisor_model">Model (optional override)</FieldLabel>
              <input
                id="advisor_model"
                type="text"
                className={cn(inputClass, "max-w-sm")}
                value={advisorModel}
                onChange={(e) => setAdvisorModel(e.target.value)}
                placeholder="e.g. mistral-large-latest"
              />
              <HelperText>Leave empty to use the provider default model</HelperText>
            </div>

            {/* Quality routing indicator */}
            {!advisorModel && (
              <div className="rounded-lg border border-border bg-muted/30 px-3 py-2.5">
                <p className="text-xs font-medium text-foreground mb-1">Quality Routing: Auto</p>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  generation=<span className="text-success font-medium">high</span>
                  {" - "}chat=<span className="text-success font-medium">high</span>
                  {" - "}explain=<span className="text-warning font-medium">medium</span>
                  {" - "}evolution=<span className="text-warning font-medium">medium</span>
                  {" - "}judge=<span className="text-muted-foreground font-medium">low</span>
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  High-stakes operations use the best model; cheap operations use the cheapest
                  model that meets the quality bar. Set a model override above to disable.
                </p>
              </div>
            )}
            {advisorModel && (
              <div className="rounded-lg border border-accent/30 bg-accent/5 px-3 py-2.5">
                <p className="text-xs font-medium text-foreground mb-0.5">Quality Routing: Disabled</p>
                <p className="text-xs text-muted-foreground">
                  All advisor calls use <span className="font-mono text-accent">{advisorModel}</span> regardless of purpose.
                  Clear the override above to re-enable automatic quality routing.
                </p>
              </div>
            )}

            {/* EU data residency toggle */}
            <div className="flex items-center gap-3">
              <input
                id="eu_mode"
                type="checkbox"
                className="h-4 w-4 rounded border-border accent-[color:var(--color-accent)] cursor-pointer"
                checked={euMode}
                onChange={(e) => setEuMode(e.target.checked)}
              />
              <label htmlFor="eu_mode" className="text-sm text-foreground cursor-pointer select-none">
                EU Data Residency - only use EU-based providers
              </label>
            </div>

            <div className="flex justify-end">
              <SaveButton
                dirty={advisorDirty}
                saving={savingAdvisor}
                onClick={() => void handleSaveAdvisor()}
              />
            </div>
          </>
        )}
      </div>
    </SectionCard>
  );
}
