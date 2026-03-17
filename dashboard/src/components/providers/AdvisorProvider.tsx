import { useMemo } from "react";
import type { ReactNode } from "react";
import { useAdvisor } from "@/hooks/useAdvisor";
import { AdvisorContext } from "@/hooks/useAdvisorContext";

interface AdvisorProviderProps {
  children: ReactNode;
}

/**
 * Provides a single shared useAdvisor instance to all descendants.
 * Prevents duplicate API calls when multiple components need advisor data.
 */
export function AdvisorProvider({ children }: AdvisorProviderProps) {
  const advisor = useAdvisor();

  const value = useMemo(
    () => ({
      score: advisor.score,
      previousScore: advisor.previousScore,
      deductions: advisor.deductions,
      activeInsights: advisor.activeInsights,
      loading: advisor.loading,
      lastChecked: advisor.lastChecked,
      errors: advisor.errors,
      refresh: advisor.refresh,
      dismiss: advisor.dismiss,
    }),
    [
      advisor.score,
      advisor.previousScore,
      advisor.deductions,
      advisor.activeInsights,
      advisor.loading,
      advisor.lastChecked,
      advisor.errors,
      advisor.refresh,
      advisor.dismiss,
    ]
  );

  return (
    <AdvisorContext.Provider value={value}>
      {children}
    </AdvisorContext.Provider>
  );
}
