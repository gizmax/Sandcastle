import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import { Suspense, lazy } from "react";
import { Layout } from "@/components/layout/Layout";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { ErrorBoundary } from "@/components/shared/ErrorBoundary";
import { EventStreamProvider } from "@/components/providers/EventStreamProvider";
import { useAuth } from "@/hooks/useAuth";
import { AuthGate } from "@/components/auth/AuthGate";

// Lazy-loaded page components for code splitting
const Overview = lazy(() => import("@/pages/Overview"));
const Runs = lazy(() => import("@/pages/Runs"));
const RunComparePage = lazy(() => import("@/pages/RunComparePage"));
const RunDetailPage = lazy(() => import("@/pages/RunDetailPage"));
const Workflows = lazy(() => import("@/pages/Workflows"));
const WorkflowBuilderPage = lazy(() => import("@/pages/WorkflowBuilderPage"));
const WorkflowDetailPage = lazy(() => import("@/pages/WorkflowDetailPage"));
const ApprovalsPage = lazy(() => import("@/pages/ApprovalsPage"));
const AutoPilotPage = lazy(() => import("@/pages/AutoPilotPage"));
const ViolationsPage = lazy(() => import("@/pages/ViolationsPage"));
const OptimizerPage = lazy(() => import("@/pages/OptimizerPage"));
const Schedules = lazy(() => import("@/pages/Schedules"));
const DeadLetterPage = lazy(() => import("@/pages/DeadLetterPage"));
const ApiKeysPage = lazy(() => import("@/pages/ApiKeysPage"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));
const TemplatesPage = lazy(() => import("@/pages/TemplatesPage"));
const IntegrationsPage = lazy(() => import("@/pages/IntegrationsPage"));
const EvaluationsPage = lazy(() => import("@/pages/EvaluationsPage"));
const SystemHealthPage = lazy(() => import("@/pages/SystemHealthPage"));
const CompliancePage = lazy(() => import("@/pages/CompliancePage"));
const Onboarding = lazy(() => import("@/pages/Onboarding"));

/** Wrap a lazy-loaded page in a per-route error boundary so a crash in one
 *  page does not take down the entire app. */
function PageBoundary({ name, children }: { name: string; children: React.ReactNode }) {
  return <ErrorBoundary name={name}>{children}</ErrorBoundary>;
}

function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 gap-4">
      <h1 className="text-4xl font-bold text-foreground">404</h1>
      <p className="text-muted">Page not found</p>
      <Link
        to="/"
        className="text-sm font-medium text-accent hover:text-accent/80 transition-colors"
      >
        Back to Overview
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
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <ErrorBoundary name="root">
      <EventStreamProvider>
        <Suspense
          fallback={
            <div className="flex h-screen items-center justify-center">
              <LoadingSpinner size="lg" />
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
              <Route path="/evaluations" element={<PageBoundary name="evaluations"><EvaluationsPage /></PageBoundary>} />
              <Route path="/autopilot" element={<PageBoundary name="autopilot"><AutoPilotPage /></PageBoundary>} />
              <Route path="/violations" element={<PageBoundary name="violations"><ViolationsPage /></PageBoundary>} />
              <Route path="/optimizer" element={<PageBoundary name="optimizer"><OptimizerPage /></PageBoundary>} />
              <Route path="/schedules" element={<PageBoundary name="schedules"><Schedules /></PageBoundary>} />
              <Route path="/dead-letter" element={<PageBoundary name="dead-letter"><DeadLetterPage /></PageBoundary>} />
              <Route path="/api-keys" element={<PageBoundary name="api-keys"><ApiKeysPage /></PageBoundary>} />
              <Route path="/settings" element={<PageBoundary name="settings"><SettingsPage /></PageBoundary>} />
              <Route path="/system-health" element={<PageBoundary name="system-health"><SystemHealthPage /></PageBoundary>} />
              <Route path="/compliance" element={<PageBoundary name="compliance"><CompliancePage /></PageBoundary>} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </Suspense>
      </EventStreamProvider>
      </ErrorBoundary>
    </BrowserRouter>
  );
}
