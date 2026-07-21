import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import { DuneContours } from "@/components/brand";

/**
 * Full-screen onboarding page - renders outside the Layout wrapper.
 * Uses bg-grid pattern background with centered max-width content.
 */
export default function Onboarding() {
  const navigate = useNavigate();

  const handleFinish = useCallback(() => {
    // Best-effort persistence: private browsing / disabled storage / quota
    // exceeded must not block the user from leaving the onboarding screen.
    try {
      localStorage.setItem("sandcastle-onboarding-done", "true");
    } catch {
      // Storage unavailable - the user will see onboarding again next time
      // but at least they can proceed now.
    }
    navigate("/");
  }, [navigate]);

  return (
    {/* Centering via my-auto on the child, NOT justify-center on the parent:
        justify-center clips the top of content taller than the viewport
        (e.g. templates with many inputs) and makes it unreachable by scroll. */}
    <div className="relative flex min-h-screen flex-col items-center bg-background bg-grid p-4 sm:p-6">
      {/* Dune contours along the bottom - quiet brand backdrop */}
      <DuneContours className="fixed inset-x-0 bottom-0 h-44 w-full text-foreground opacity-[0.06] sm:h-56" />

      {/* Subtle brand in top-left */}
      <div className="fixed top-4 left-4 flex items-center gap-2 text-sm font-semibold text-foreground/60">
        <span className="text-lg">🏰</span>
        Sandcastle
      </div>

      {/* Centered wizard container */}
      <div className="relative my-auto w-full max-w-[720px] py-4">
        <OnboardingWizard onFinish={handleFinish} />
      </div>
    </div>
  );
}
