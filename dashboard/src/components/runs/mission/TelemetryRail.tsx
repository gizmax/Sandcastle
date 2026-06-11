import { useEffect, useRef, useState } from "react";
import { Coins, Cpu, Gauge, Layers, Timer } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatClock, type Telemetry } from "@/lib/missionControl";

/* ── Animated odometer ── */

interface OdometerProps {
  value: number;
  decimals?: number;
  prefix?: string;
  className?: string;
  durationMs?: number;
}

/**
 * Odometer-style number that eases toward its target value.
 * Renders the final value immediately on first mount (no fake count-up),
 * then animates on subsequent changes. Tabular numerals prevent layout
 * shift while ticking.
 */
export function Odometer({ value, decimals = 2, prefix = "", className, durationMs = 700 }: OdometerProps) {
  const [display, setDisplay] = useState(value);
  const fromRef = useRef(value);
  const rafRef = useRef<number | null>(null);
  const mountedRef = useRef(false);

  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      fromRef.current = value;
      setDisplay(value);
      return;
    }
    const from = fromRef.current;
    if (from === value) return;
    const start = performance.now();

    const tick = (now: number) => {
      const t = Math.min((now - start) / durationMs, 1);
      // ease-out cubic
      const eased = 1 - Math.pow(1 - t, 3);
      const current = from + (value - from) * eased;
      setDisplay(current);
      if (t < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        fromRef.current = value;
        rafRef.current = null;
      }
    };

    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      fromRef.current = value;
    };
  }, [value, durationMs]);

  return (
    <span className={cn("font-data", className)}>
      {prefix}
      {display.toLocaleString("en-US", {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
    </span>
  );
}

/* ── Rail ── */

interface TelemetryRailProps {
  telemetry: Telemetry;
  throughput: number;
  elapsedSeconds: number;
  isLive: boolean;
}

function StatLabel({ icon: Icon, children }: { icon: typeof Coins; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      <Icon className="h-3 w-3" />
      {children}
    </div>
  );
}

export function TelemetryRail({ telemetry, throughput, elapsedSeconds, isLive }: TelemetryRailProps) {
  const { costUsd, tokensEst, stepsTotal, stepsDone, stepsFailed, models } = telemetry;
  const finishedSteps = stepsDone + stepsFailed;
  const progressPct = stepsTotal > 0 ? (finishedSteps / stepsTotal) * 100 : 0;
  const isFree = costUsd === 0;

  return (
    <div className="shrink-0 space-y-3 border-b border-border p-4">
      {/* Cost - the hero number */}
      <div className="rounded-xl border border-border bg-surface p-4">
        <div className="flex items-center justify-between">
          <StatLabel icon={Coins}>Run cost</StatLabel>
          {isFree && (
            <span className="rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-success">
              Free
            </span>
          )}
        </div>
        <div className="mt-1.5">
          <Odometer
            value={costUsd}
            decimals={isFree ? 2 : 4}
            prefix="$"
            className={cn(
              "text-3xl font-semibold tracking-tight",
              isFree ? "text-success" : "text-accent"
            )}
          />
        </div>
        {isFree && (
          <p className="mt-1 text-[11px] text-muted">No API spend on this run.</p>
        )}
      </div>

      {/* Secondary stats grid */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-border bg-surface p-3">
          <StatLabel icon={Gauge}>Tokens</StatLabel>
          <div className="mt-1">
            <Odometer value={tokensEst} decimals={0} className="text-lg font-semibold text-foreground" />
            <span className="ml-1 text-[10px] text-muted-foreground">est.</span>
          </div>
          <p className="font-data mt-0.5 text-[11px] text-muted">
            {isLive && throughput > 0 ? `${Math.round(throughput)} tok/s` : "—"}
          </p>
        </div>

        <div className="rounded-xl border border-border bg-surface p-3">
          <StatLabel icon={Timer}>Elapsed</StatLabel>
          <div className="font-data mt-1 text-lg font-semibold text-foreground">
            {formatClock(elapsedSeconds)}
          </div>
          <p className="mt-0.5 text-[11px] text-muted">{isLive ? "live" : "final"}</p>
        </div>
      </div>

      {/* Step progress */}
      <div className="rounded-xl border border-border bg-surface p-3">
        <div className="flex items-center justify-between">
          <StatLabel icon={Layers}>Steps</StatLabel>
          <span className="font-data text-sm font-semibold text-foreground">
            {finishedSteps}/{stepsTotal}
          </span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-border/60">
          <div
            className={cn(
              "h-full rounded-full transition-[width] duration-500 ease-out",
              stepsFailed > 0 ? "bg-error" : "bg-success"
            )}
            style={{ width: `${progressPct}%` }}
          />
        </div>
        {stepsFailed > 0 && (
          <p className="mt-1.5 text-[11px] text-error">
            {stepsFailed} step{stepsFailed > 1 ? "s" : ""} failed
          </p>
        )}
      </div>

      {/* Models in use */}
      <div className="rounded-xl border border-border bg-surface p-3">
        <StatLabel icon={Cpu}>Models</StatLabel>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {models.length === 0 ? (
            <span className="text-[11px] text-muted">—</span>
          ) : (
            models.map((model) => (
              <span
                key={model}
                className="rounded-md bg-accent/10 px-2 py-0.5 font-mono text-[10px] font-medium text-accent"
              >
                {model}
              </span>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
