import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "@/components/layout/Layout";
import Overview from "@/pages/Overview";
import Runs from "@/pages/Runs";
import RunDetailPage from "@/pages/RunDetailPage";
import Workflows from "@/pages/Workflows";
import WorkflowBuilderPage from "@/pages/WorkflowBuilderPage";
import ApprovalsPage from "@/pages/ApprovalsPage";
import AutoPilotPage from "@/pages/AutoPilotPage";
import Schedules from "@/pages/Schedules";
import DeadLetterPage from "@/pages/DeadLetterPage";
import Onboarding from "@/pages/Onboarding";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetailPage />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/workflows/builder" element={<WorkflowBuilderPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/autopilot" element={<AutoPilotPage />} />
          <Route path="/schedules" element={<Schedules />} />
          <Route path="/dead-letter" element={<DeadLetterPage />} />
          <Route path="/onboarding" element={<Onboarding />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
