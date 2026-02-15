import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";

export default function Onboarding() {
  const navigate = useNavigate();

  const handleFinish = useCallback(() => {
    localStorage.setItem("sandcastle-onboarding-done", "true");
    navigate("/");
  }, [navigate]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <OnboardingWizard onFinish={handleFinish} />
    </div>
  );
}
