import { BrowserRouter, Routes, Route, Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Suspense, useEffect } from "react";
import { Layout } from "@/components/layout/Layout";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { EventStreamProvider } from "@/components/providers/EventStreamProvider";
import { UiModeProvider, useUiMode } from "@/contexts/UiModeContext";
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
const ApiKeysPage = lazyWithRetry(() => import("@/pages/ApiKeysPage"), "api-keys");
const SettingsPage = lazyWithRetry(() => import("@/pages/SettingsPage"), "settings");
const TemplatesPage = lazyWithRetry(() => import("@/pages/TemplatesPage"), "templates");
const IntegrationsPage = lazyWithRetry(() => import("@/pages/IntegrationsPage"), "integrations");
const EvaluationsPage = lazyWithRetry(() => import("@/pages/EvaluationsPage"), "evaluations");
const EvolutionPage = lazyWithRetry(() => import("@/pages/EvolutionPage"), "evolution");
const SystemHealthPage = lazyWithRetry(() => import("@/pages/SystemHealthPage"), "system-health");
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

/** In Lite mode, advanced pages are hidden - redirect their routes to Overview. */
function LiteGuard({ children }: { children: React.ReactNode }) {
  const { isLite } = useUiMode();
  if (isLite) return <Navigate to="/" replace />;
  return <>{children}</>;
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

            <Route element={<Layout />}>
              <Route path="/" element={<PageBoundary name="overview"><Overview /></PageBoundary>} />
              <Route path="/runs" element={<PageBoundary name="runs"><Runs /></PageBoundary>} />
              <Route path="/runs/compare" element={<PageBoundary name="run-compare"><RunComparePage /></PageBoundary>} />
              <Route path="/runs/:id" element={<PageBoundary name="run-detail"><RunDetailPage /></PageBoundary>} />
              <Route path="/workflows" element={<PageBoundary name="workflows"><Workflows /></PageBoundary>} />
              <Route path="/workflows/builder" element={<PageBoundary name="workflow-builder"><WorkflowBuilderPage /></PageBoundary>} />
              <Route path="/workflows/:name" element={<PageBoundary name="workflow-detail"><WorkflowDetailPage /></PageBoundary>} />
              <Route path="/templates" element={<PageBoundary name="templates"><TemplatesPage /></PageBoundary>} />
              <Route path="/integrations" element={<PageBoundary name="integrations"><IntegrationsPage /></PageBoundary>} />
              <Route path="/approvals" element={<PageBoundary name="approvals"><ApprovalsPage /></PageBoundary>} />
              <Route path="/evaluations" element={<LiteGuard><PageBoundary name="evaluations"><EvaluationsPage /></PageBoundary></LiteGuard>} />
              <Route path="/autopilot" element={<LiteGuard><PageBoundary name="autopilot"><AutoPilotPage /></PageBoundary></LiteGuard>} />
              <Route path="/evolution" element={<LiteGuard><PageBoundary name="evolution"><EvolutionPage /></PageBoundary></LiteGuard>} />
              <Route path="/violations" element={<LiteGuard><PageBoundary name="violations"><ViolationsPage /></PageBoundary></LiteGuard>} />
              <Route path="/optimizer" element={<LiteGuard><PageBoundary name="optimizer"><OptimizerPage /></PageBoundary></LiteGuard>} />
              <Route path="/schedules" element={<PageBoundary name="schedules"><Schedules /></PageBoundary>} />
              <Route path="/schedule-monitor" element={<LiteGuard><PageBoundary name="schedule-monitor"><ScheduleMonitorPage /></PageBoundary></LiteGuard>} />
              <Route path="/dead-letter" element={<LiteGuard><PageBoundary name="dead-letter"><DeadLetterPage /></PageBoundary></LiteGuard>} />
              <Route path="/api-keys" element={<LiteGuard><PageBoundary name="api-keys"><ApiKeysPage /></PageBoundary></LiteGuard>} />
              <Route path="/settings" element={<PageBoundary name="settings"><SettingsPage /></PageBoundary>} />
              <Route path="/system-health" element={<PageBoundary name="system-health"><SystemHealthPage /></PageBoundary>} />
              <Route path="/compliance" element={<LiteGuard><PageBoundary name="compliance"><CompliancePage /></PageBoundary></LiteGuard>} />
              <Route path="/memory" element={<LiteGuard><PageBoundary name="memory"><MemoryPage /></PageBoundary></LiteGuard>} />
              <Route path="/time-machine" element={<LiteGuard><PageBoundary name="time-machine"><TimeMachinePage /></PageBoundary></LiteGuard>} />
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
