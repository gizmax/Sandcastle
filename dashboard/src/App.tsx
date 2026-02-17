import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Suspense, lazy } from "react";
import { Layout } from "@/components/layout/Layout";
import { LoadingSpinner } from "@/components/shared/LoadingSpinner";
import { EventStreamProvider } from "@/components/providers/EventStreamProvider";

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
const Onboarding = lazy(() => import("@/pages/Onboarding"));

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <EventStreamProvider>
        <Suspense
          fallback={
            <div className="flex h-screen items-center justify-center">
              <LoadingSpinner size="lg" />
            </div>
          }
        >
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Overview />} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/compare" element={<RunComparePage />} />
              <Route path="/runs/:id" element={<RunDetailPage />} />
              <Route path="/workflows" element={<Workflows />} />
              <Route path="/workflows/builder" element={<WorkflowBuilderPage />} />
              <Route path="/workflows/:name" element={<WorkflowDetailPage />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/autopilot" element={<AutoPilotPage />} />
              <Route path="/violations" element={<ViolationsPage />} />
              <Route path="/optimizer" element={<OptimizerPage />} />
              <Route path="/schedules" element={<Schedules />} />
              <Route path="/dead-letter" element={<DeadLetterPage />} />
              <Route path="/api-keys" element={<ApiKeysPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/onboarding" element={<Onboarding />} />
            </Route>
          </Routes>
        </Suspense>
      </EventStreamProvider>
    </BrowserRouter>
  );
}
