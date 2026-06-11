import { BrowserRouter, Routes, Route, Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Suspense, useEffect } from "react";
import { Layout } from "@/components/layout/Layout";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { EventStreamProvider } from "@/components/providers/EventStreamProvider";
import { UiModeProvider, useDensity, type Density } from "@/contexts/UiModeContext";
import { useAuth } from "@/hooks/useAuth";
import { AuthGate } from "@/components/auth/AuthGate";
import { usePageTracking } from "@/hooks/usePageTracking";
import { lazyWithRetry } from "@/lib/lazyWithRetry";
import { SandcastleRuin, DuneContours, BuildingLoader } from "@/components/brand";

// Lazy-loaded page components for code splitting. lazyWithRetry recovers from a
// stale-deploy chunk miss (open tab + new deploy = 404 on the old hashed chunk)
// by reloading once for the fresh index.html, instead of dead-ending the route.
const Overview = lazyWithRetry(() => import("@/pages/Overview"), "overview");
const Runs = lazyWithRetry(() => import("@/pages/Runs"), "runs");
const RunComparePage = lazyWithRetry(() => import("@/pages/RunComparePage"), "run-compare");
const RunDetailPage = lazyWithRetry(() => import("@/pages/RunDetailPage"), "run-detail");
const MissionControlPage = lazyWithRetry(() => import("@/pages/MissionControlPage"), "mission-control");
const Workflows = lazyWithRetry(() => import("@/pages/Workflows"), "workflows");
const WorkflowBuilderPage = lazyWithRetry(() => import("@/pages/WorkflowBuilderPage"), "workflow-builder");
const WorkflowDetailPage = lazyWithRetry(() => import("@/pages/WorkflowDetailPage"), "workflow-detail");
const ApprovalsPage = lazyWithRetry(() => import("@/pages/ApprovalsPage"), "approvals");
const AutoPilotPage = lazyWithRetry(() => import("@/pages/AutoPilotPage"), "autopilot");
const ViolationsPage = lazyWithRetry(() => import("@/pages/ViolationsPage"), "violations");
const OptimizerPage = lazyWithRetry(() => import("@/pages/OptimizerPage"), "optimizer");
const Schedules = lazyWithRetry(() => import("@/pages/Schedules"), "schedules");
const ScheduleMonitorPage = lazyWithRetry(() => import("@/pages/ScheduleMonitorPage"), "schedule-monitor");
const DeadLetterPage = lazyWithRetry(() => import("@/pages/DeadLetterPage"), "dead-letter");
const SettingsPage = lazyWithRetry(() => import("@/pages/SettingsPage"), "settings");
const TemplatesPage = lazyWithRetry(() => import("@/pages/TemplatesPage"), "templates");
// NOTE: ApiKeysPage and IntegrationsPage are no longer mounted as standalone
// routes — their paths redirect into the Settings hub (/settings?tab=keys|
// integrations). The page components remain in src/pages for the downstream
// Settings agent to render as tab content.
const EvaluationsPage = lazyWithRetry(() => import("@/pages/EvaluationsPage"), "evaluations");
const EvolutionPage = lazyWithRetry(() => import("@/pages/EvolutionPage"), "evolution");
const NightShiftPage = lazyWithRetry(() => import("@/pages/NightShiftPage"), "night-shift");
const SystemHealthPage = lazyWithRetry(() => import("@/pages/SystemHealthPage"), "system-health");
const FleetPage = lazyWithRetry(() => import("@/pages/FleetPage"), "fleet");
const CompliancePage = lazyWithRetry(() => import("@/pages/CompliancePage"), "compliance");
const MemoryPage = lazyWithRetry(() => import("@/pages/MemoryPage"), "memory");
const TimeMachinePage = lazyWithRetry(() => import("@/pages/TimeMachinePage"), "time-machine");
const Onboarding = lazyWithRetry(() => import("@/pages/Onboarding"), "onboarding");

/** Wrap a lazy-loaded page in a per-route error boundary so a crash in one
 *  page does not take down the entire app. */
function PageBoundary({ name, children }: { name: string; children: React.ReactNode }) {
  return <ErrorBoundary name={name}>{children}</ErrorBoundary>;
}

/** Invisible component that tracks page views inside the router context. */
function PageTracker() {
  usePageTracking();
  return null;
}

/** Redirect brand-new installs (onboarding not completed) to the guided wizard. */
function FirstRunGuard() {
  const location = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    let done = true;
    try {
      done = localStorage.getItem("sandcastle-onboarding-done") === "true";
    } catch {
      done = true; // storage unavailable - don't trap the user in a redirect loop
    }
    if (!done && location.pathname !== "/onboarding") {
      navigate("/onboarding", { replace: true });
    }
  }, [location.pathname, navigate]);
  return null;
}

/**
 * Density-tier route guard. Pages in the IMPROVE + OPERATE nav groups require
 * at least the given density tier; below it, the route redirects to Home.
 * Default `min` is "Standard" (the tier at which IMPROVE/OPERATE become visible).
 */
function TierGuard({
  children,
  min = "Standard",
}: {
  children: React.ReactNode;
  min?: Density;
}) {
  const { atLeast } = useDensity();
  if (!atLeast(min)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

/**
 * Settings hub redirect. Legacy standalone pages (/api-keys, /integrations,
 * /providers) now live as tabs inside SettingsPage. Preserve old links by
 * 301-style redirecting to /settings?tab=<tab>.
 */
function SettingsRedirect({ tab }: { tab: string }) {
  return <Navigate to={`/settings?tab=${tab}`} replace />;
}

function NotFound() {
  return (
    <div className="relative flex flex-col items-center justify-center gap-3 overflow-hidden py-24 text-center">
      <DuneContours className="absolute inset-x-0 bottom-0 h-40 w-full text-foreground opacity-[0.05]" />
      <SandcastleRuin title="A crumbled sandcastle" className="h-36 w-48 text-muted-foreground" />
      <h1 className="font-display text-3xl font-bold tracking-tight text-foreground">
        This castle washed away.
      </h1>
      <p className="max-w-sm text-sm text-muted">
        404 - the page you're looking for isn't here. The tide got it.
      </p>
      <Link
        to="/"
        className="relative mt-3 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground shadow-sm transition-all hover:bg-accent-hover hover:shadow-md"
      >
        Back to solid ground
      </Link>
    </div>
  );
}

export default function App() {
  const { state, login } = useAuth();

  if (state === "loading") {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <LoadingSpinner size="lg" />
      </div>
    );
  }

  if (state === "unauthenticated") {
    return <AuthGate onLogin={login} />;
  }

  return (
    <UiModeProvider>
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <PageTracker />
      <FirstRunGuard />
      {state === "offline" && (
        <div className="bg-yellow-500/90 text-yellow-950 text-center text-sm py-1.5 px-4 font-medium">
          Backend unreachable - running in offline/demo mode. Data shown may be stale.
        </div>
      )}
      <ErrorBoundary name="root">
      <EventStreamProvider>
        <Suspense
          fallback={
            <div className="flex h-screen items-center justify-center">
              <BuildingLoader />
            </div>
          }
        >
          <Routes>
            {/* Onboarding lives outside Layout (full-screen, no sidebar) */}
            <Route path="/onboarding" element={<PageBoundary name="onboarding"><Onboarding /></PageBoundary>} />

            {/* Mission Control lives outside Layout (full-bleed live run theater) */}
            <Route path="/runs/:id/live" element={<PageBoundary name="mission-control"><MissionControlPage /></PageBoundary>} />

            {/*
             * ============================================================
             * ROUTE MAP — grouped by the new 3-verb IA (see Sidebar.tsx).
             * HOME / BUILD / RUN / IMPROVE* / OPERATE* / SETTINGS  (*Standard+ gated)
             * REDIRECTS: /api-keys|/integrations|/providers -> /settings?tab=...
             * ============================================================
             */}
            <Route element={<Layout />}>
              {/* HOME */}
              <Route path="/" element={<PageBoundary name="overview"><Overview /></PageBoundary>} />

              {/* RUN */}
              <Route path="/runs" element={<PageBoundary name="runs"><Runs /></PageBoundary>} />
              <Route path="/runs/compare" element={<PageBoundary name="run-compare"><RunComparePage /></PageBoundary>} />
              <Route path="/runs/:id" element={<PageBoundary name="run-detail"><RunDetailPage /></PageBoundary>} />
              <Route path="/approvals" element={<PageBoundary name="approvals"><ApprovalsPage /></PageBoundary>} />
              <Route path="/schedules" element={<PageBoundary name="schedules"><Schedules /></PageBoundary>} />

              {/* BUILD */}
              <Route path="/workflows" element={<PageBoundary name="workflows"><Workflows /></PageBoundary>} />
              <Route path="/workflows/builder" element={<PageBoundary name="workflow-builder"><WorkflowBuilderPage /></PageBoundary>} />
              <Route path="/workflows/:name" element={<PageBoundary name="workflow-detail"><WorkflowDetailPage /></PageBoundary>} />
              <Route path="/templates" element={<PageBoundary name="templates"><TemplatesPage /></PageBoundary>} />

              {/* IMPROVE — Standard+ */}
              <Route path="/evolution" element={<TierGuard><PageBoundary name="evolution"><EvolutionPage /></PageBoundary></TierGuard>} />
              <Route path="/autopilot" element={<TierGuard><PageBoundary name="autopilot"><AutoPilotPage /></PageBoundary></TierGuard>} />
              <Route path="/optimizer" element={<TierGuard><PageBoundary name="optimizer"><OptimizerPage /></PageBoundary></TierGuard>} />
              <Route path="/evaluations" element={<TierGuard><PageBoundary name="evaluations"><EvaluationsPage /></PageBoundary></TierGuard>} />
              <Route path="/memory" element={<TierGuard><PageBoundary name="memory"><MemoryPage /></PageBoundary></TierGuard>} />
              <Route path="/night-shift" element={<TierGuard><PageBoundary name="night-shift"><NightShiftPage /></PageBoundary></TierGuard>} />
              <Route path="/time-machine" element={<TierGuard><PageBoundary name="time-machine"><TimeMachinePage /></PageBoundary></TierGuard>} />

              {/* OPERATE — Standard+ */}
              <Route path="/system-health" element={<TierGuard><PageBoundary name="system-health"><SystemHealthPage /></PageBoundary></TierGuard>} />
              <Route path="/dead-letter" element={<TierGuard><PageBoundary name="dead-letter"><DeadLetterPage /></PageBoundary></TierGuard>} />
              <Route path="/violations" element={<TierGuard><PageBoundary name="violations"><ViolationsPage /></PageBoundary></TierGuard>} />
              <Route path="/compliance" element={<TierGuard><PageBoundary name="compliance"><CompliancePage /></PageBoundary></TierGuard>} />
              <Route path="/schedule-monitor" element={<TierGuard><PageBoundary name="schedule-monitor"><ScheduleMonitorPage /></PageBoundary></TierGuard>} />
              <Route path="/fleet" element={<TierGuard><PageBoundary name="fleet"><FleetPage /></PageBoundary></TierGuard>} />

              {/* SETTINGS hub — supports ?tab=general|keys|providers|integrations|advanced. */}
              <Route path="/settings" element={<PageBoundary name="settings"><SettingsPage /></PageBoundary>} />

              {/* REDIRECTS — old standalone routes fold into the Settings hub. */}
              <Route path="/api-keys" element={<SettingsRedirect tab="keys" />} />
              <Route path="/integrations" element={<SettingsRedirect tab="integrations" />} />
              <Route path="/providers" element={<SettingsRedirect tab="providers" />} />

              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </Suspense>
      </EventStreamProvider>
      </ErrorBoundary>
    </BrowserRouter>
    </UiModeProvider>
  );
}
