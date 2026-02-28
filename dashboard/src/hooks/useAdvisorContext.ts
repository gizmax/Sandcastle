import { createContext, useContext } from "react";
import type { Deduction, Insight } from "@/lib/insights";

export interface AdvisorContextValue {
  score: number;
  previousScore: number | null;
  deductions: Deduction[];
  activeInsights: Insight[];
  loading: boolean;
  lastChecked: Date | null;
  refresh: () => Promise<void>;
  dismiss: (id: string) => void;
}

export const AdvisorContext = createContext<AdvisorContextValue | null>(null);

/**
 * Access the shared advisor context.
 * Must be used within an AdvisorProvider (rendered in Layout).
 */
export function useAdvisorContext(): AdvisorContextValue {
  const ctx = useContext(AdvisorContext);
  if (!ctx) {
    throw new Error("useAdvisorContext must be used within AdvisorProvider");
  }
  return ctx;
}
