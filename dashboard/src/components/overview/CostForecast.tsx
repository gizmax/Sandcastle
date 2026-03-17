import { useState, useEffect } from "react";
import { TrendingUp, TrendingDown, AlertTriangle, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";

interface DayData {
  date: string;
  cost: number;
  projected: boolean;
}

interface ForecastResponse {
  historical: { date: string; cost: number; runs: number }[];
  projected: { date: string; cost: number }[];
  daily_average: number;
  trend_percent: number;
  projected_monthly: number;
}

function movingAverage(values: number[], window: number): (number | null)[] {
  return values.map((_, i) => {
    if (i < window - 1) return null;
    let sum = 0;
    for (let j = i - window + 1; j <= i; j++) sum += values[j];
    return sum / window;
  });
}

export function CostForecast() {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [forecast, setForecast] = useState<ForecastResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await api.get<ForecastResponse>("/stats/forecast");
        if (!cancelled) {
          if (resp.error) {
            setError(resp.error.message || "Failed to load forecast");
          } else {
            setForecast(resp.data ?? null);
          }
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError("Failed to load forecast");
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/10">
            <TrendingUp className="h-3.5 w-3.5 text-accent" />
          </div>
          <h3 className="text-sm font-medium text-foreground">Cost Forecast</h3>
        </div>
        <div className="flex items-center justify-center h-[200px]">
          <Loader2 className="h-5 w-5 animate-spin text-muted" />
        </div>
      </div>
    );
  }

  if (error || !forecast) {
    return (
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/10">
            <TrendingUp className="h-3.5 w-3.5 text-accent" />
          </div>
          <h3 className="text-sm font-medium text-foreground">Cost Forecast</h3>
        </div>
        <p className="text-xs text-muted text-center py-8">{error || "No data"}</p>
      </div>
    );
  }

  // Build unified data array
  const data: DayData[] = [
    ...forecast.historical.map((h) => ({ date: h.date, cost: h.cost, projected: false })),
    ...forecast.projected.map((p) => ({ date: p.date, cost: p.cost, projected: true })),
  ];

  if (data.length === 0) {
    return (
      <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
        <div className="flex items-center gap-2 mb-3">
          <div className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/10">
            <TrendingUp className="h-3.5 w-3.5 text-accent" />
          </div>
          <h3 className="text-sm font-medium text-foreground">Cost Forecast</h3>
        </div>
        <p className="text-xs text-muted text-center py-8">No cost data yet. Run some workflows to see forecasts.</p>
      </div>
    );
  }

  const allCosts = data.map((d) => d.cost);
  const historicalCosts = data.filter((d) => !d.projected).map((d) => d.cost);
  const ma7 = movingAverage(historicalCosts, 7);

  const thisMonthTotal = historicalCosts.reduce((s, c) => s + c, 0);
  const projectedMonthly = forecast.projected_monthly;
  const trendPct = forecast.trend_percent;

  const BUDGET_THRESHOLD = 100;
  const overBudget = projectedMonthly > BUDGET_THRESHOLD;

  const W = 600;
  const H = 200;
  const PAD_L = 45;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 28;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  const minCost = Math.min(...allCosts) * 0.8;
  const maxCost = Math.max(...allCosts) * 1.2;
  const costRange = maxCost - minCost || 1;

  const xStep = data.length > 1 ? chartW / (data.length - 1) : chartW;

  const toX = (i: number) => PAD_L + i * xStep;
  const toY = (v: number) => PAD_T + chartH - ((v - minCost) / costRange) * chartH;

  const historicalPoints = data
    .map((d, i) => (!d.projected ? { x: toX(i), y: toY(d.cost) } : null))
    .filter(Boolean) as { x: number; y: number }[];

  const projectedPoints = data
    .map((d, i) => (d.projected || i === data.length - 8 ? { x: toX(i), y: toY(d.cost) } : null))
    .filter(Boolean) as { x: number; y: number }[];

  const historicalPath = historicalPoints.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  const projectedPath = projectedPoints.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");

  const projectionAreaPath =
    projectedPoints.length > 1
      ? [
          `M${projectedPoints[0].x},${toY(0)}`,
          ...projectedPoints.map((p) => `L${p.x},${p.y}`),
          `L${projectedPoints[projectedPoints.length - 1].x},${toY(0)}`,
          "Z",
        ].join(" ")
      : "";

  const yTicks: number[] = [];
  const tickStep = costRange / 4;
  for (let i = 0; i <= 4; i++) {
    yTicks.push(minCost + tickStep * i);
  }

  const gridLines = yTicks.map((v) => toY(v));
  const xLabelInterval = Math.max(1, Math.floor(data.length / 6));
  const splitIdx = data.findIndex((d) => d.projected);

  const ma7Points = ma7
    .map((v, i) => (v !== null ? { x: toX(i), y: toY(v) } : null))
    .filter(Boolean) as { x: number; y: number }[];
  const ma7Path = ma7Points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");

  return (
    <div className="rounded-xl border border-border bg-surface p-4 sm:p-5 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/10">
          <TrendingUp className="h-3.5 w-3.5 text-accent" />
        </div>
        <h3 className="text-sm font-medium text-foreground">Cost Forecast</h3>
      </div>

      <div className="relative overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          style={{ minWidth: "400px", maxHeight: "300px" }}
          onMouseLeave={() => setHoveredIdx(null)}
        >
          {gridLines.map((y, i) => (
            <line
              key={i}
              x1={PAD_L}
              y1={y}
              x2={W - PAD_R}
              y2={y}
              stroke="var(--color-border)"
              strokeOpacity={0.3}
              strokeWidth={0.5}
            />
          ))}

          {yTicks.map((v, i) => (
            <text
              key={i}
              x={PAD_L - 6}
              y={toY(v) + 3}
              textAnchor="end"
              fontSize={9}
              fill="var(--color-muted)"
              fontFamily="'IBM Plex Mono', monospace"
            >
              ${v.toFixed(1)}
            </text>
          ))}

          {data.map((d, i) =>
            i % xLabelInterval === 0 ? (
              <text
                key={i}
                x={toX(i)}
                y={H - 4}
                textAnchor="middle"
                fontSize={8}
                fill="var(--color-muted)"
                fontFamily="'IBM Plex Mono', monospace"
              >
                {d.date.slice(5)}
              </text>
            ) : null
          )}

          {splitIdx > 0 && (
            <line
              x1={toX(splitIdx - 1)}
              y1={PAD_T}
              x2={toX(splitIdx - 1)}
              y2={PAD_T + chartH}
              stroke="var(--color-border)"
              strokeOpacity={0.4}
              strokeDasharray="3 3"
              strokeWidth={0.5}
            />
          )}

          {projectionAreaPath && (
            <path
              d={projectionAreaPath}
              fill="var(--color-accent)"
              fillOpacity={0.08}
            />
          )}

          {ma7Path && (
            <path
              d={ma7Path}
              fill="none"
              stroke="var(--color-success)"
              strokeWidth={1}
              strokeOpacity={0.5}
            />
          )}

          <path
            d={historicalPath}
            fill="none"
            stroke="var(--color-foreground)"
            strokeWidth={1.5}
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          <path
            d={projectedPath}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {data.map((_, i) => (
            <rect
              key={i}
              x={toX(i) - xStep / 2}
              y={PAD_T}
              width={xStep}
              height={chartH}
              fill="transparent"
              onMouseEnter={() => setHoveredIdx(i)}
            />
          ))}

          {hoveredIdx !== null && (
            <>
              <line
                x1={toX(hoveredIdx)}
                y1={PAD_T}
                x2={toX(hoveredIdx)}
                y2={PAD_T + chartH}
                stroke="var(--color-muted)"
                strokeOpacity={0.4}
                strokeWidth={0.5}
                strokeDasharray="2 2"
              />
              <circle
                cx={toX(hoveredIdx)}
                cy={toY(data[hoveredIdx].cost)}
                r={3}
                fill={data[hoveredIdx].projected ? "var(--color-accent)" : "var(--color-foreground)"}
                stroke="var(--color-surface)"
                strokeWidth={1.5}
              />
            </>
          )}
        </svg>

        {hoveredIdx !== null && (
          <div
            className="pointer-events-none absolute z-10 rounded-md bg-foreground px-2 py-1 text-[11px] font-medium text-background shadow-lg"
            style={{
              left: `${(toX(hoveredIdx) / W) * 100}%`,
              top: `${(toY(data[hoveredIdx].cost) / H) * 100 - 8}%`,
              transform: "translate(-50%, -100%)",
            }}
          >
            {data[hoveredIdx].date}: ${data[hoveredIdx].cost.toFixed(2)}
            {data[hoveredIdx].projected ? " (proj)" : ""}
          </div>
        )}
      </div>

      <div className="mt-2 flex items-center gap-1.5 text-[10px] text-muted">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-[2px] w-3 rounded-full bg-foreground" />
          Actual
        </span>
        <span className="inline-flex items-center gap-1 ml-2">
          <span className="inline-block h-[2px] w-3 rounded-full bg-accent" style={{ borderTop: "1px dashed" }} />
          Projected
        </span>
        <span className="inline-flex items-center gap-1 ml-2">
          <span className="inline-block h-[2px] w-3 rounded-full bg-success/50" />
          7-day MA
        </span>
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3">
        <div className="rounded-lg border border-border/50 bg-background px-3 py-2">
          <p className="text-[10px] font-medium text-muted">Last 30 Days</p>
          <p className="font-data text-base font-semibold text-foreground">${thisMonthTotal.toFixed(2)}</p>
        </div>
        <div className="rounded-lg border border-border/50 bg-background px-3 py-2">
          <p className="text-[10px] font-medium text-muted">Projected</p>
          <p className="font-data text-base font-semibold text-foreground">${projectedMonthly.toFixed(2)}/mo</p>
        </div>
        <div className="rounded-lg border border-border/50 bg-background px-3 py-2">
          <p className="text-[10px] font-medium text-muted">Trend</p>
          <div className="flex items-center gap-1">
            {trendPct > 0 ? (
              <TrendingUp className="h-3.5 w-3.5 text-error" />
            ) : (
              <TrendingDown className="h-3.5 w-3.5 text-success" />
            )}
            <p
              className={cn(
                "font-data text-base font-semibold",
                trendPct > 0 ? "text-error" : "text-success"
              )}
            >
              {trendPct > 0 ? "+" : ""}
              {trendPct.toFixed(1)}%
            </p>
          </div>
        </div>
      </div>

      {overBudget && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2">
          <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
          <p className="text-xs text-warning">
            At current rate, monthly cost will exceed ${BUDGET_THRESHOLD}
          </p>
        </div>
      )}
    </div>
  );
}
