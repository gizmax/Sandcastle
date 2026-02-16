import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import Overview from "@/pages/Overview";
import Runs from "@/pages/Runs";
import RunDetailPage from "@/pages/RunDetailPage";
import Workflows from "@/pages/Workflows";
import WorkflowBuilderPage from "@/pages/WorkflowBuilderPage";
import ApprovalsPage from "@/pages/ApprovalsPage";
import AutoPilotPage from "@/pages/AutoPilotPage";
import ViolationsPage from "@/pages/ViolationsPage";
import OptimizerPage from "@/pages/OptimizerPage";
import Schedules from "@/pages/Schedules";
import DeadLetterPage from "@/pages/DeadLetterPage";
import ApiKeysPage from "@/pages/ApiKeysPage";
import SettingsPage from "@/pages/SettingsPage";
import Onboarding from "@/pages/Onboarding";

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/workflows/builder" element={<WorkflowBuilderPage />} />
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
    </BrowserRouter>
  );
}
