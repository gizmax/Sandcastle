import { describe, it, expect } from "vitest";
import {
  STEP_TYPE_METADATA,
  STEP_TYPES,
  AGENT_TEMPLATE_METADATA,
  getStepMeta,
  getAgentTemplateMeta,
} from "@/lib/builder/stepMetadata";

// The full StepType union from StepConfigPanel — every entry must have metadata.
const ALL_STEP_TYPES = [
  "standard",
  "llm",
  "http",
  "code",
  "condition",
  "classify",
  "loop",
  "approval",
  "sub_workflow",
  "race",
  "sensor",
  "gate",
  "transform",
  "notify",
  "delegate",
  "browser",
  "parse",
  "openclaw",
  "composio",
  "agent",
  "managed-agent",
  "report",
] as const;

const ALL_AGENT_TEMPLATES = [
  "researcher",
  "coder",
  "analyst",
  "writer",
  "reviewer",
  "scraper",
  "tester",
  "devops",
  "translator",
  "designer",
  "sql_expert",
  "seo_specialist",
  "financial_analyst",
  "legal_analyst",
  "project_manager",
] as const;

describe("STEP_TYPE_METADATA", () => {
  it("has an entry for every StepType", () => {
    for (const type of ALL_STEP_TYPES) {
      expect(STEP_TYPE_METADATA[type], `missing meta for ${type}`).toBeDefined();
    }
  });

  it("STEP_TYPES lists every covered type", () => {
    expect(new Set(STEP_TYPES)).toEqual(new Set(ALL_STEP_TYPES));
  });

  it("each entry has required, non-empty fields", () => {
    for (const type of ALL_STEP_TYPES) {
      const m = STEP_TYPE_METADATA[type];
      expect(m.type).toBe(type);
      expect(m.label.length).toBeGreaterThan(0);
      expect(m.summary.length).toBeGreaterThan(0);
      expect(m.whenToUse.length).toBeGreaterThan(0);
      expect(m.example.length).toBeGreaterThan(0);
      expect(m.iconKey.length).toBeGreaterThan(0);
      expect(["AI", "Control Flow", "Integration", "Output", "Agents"]).toContain(
        m.category,
      );
    }
  });
});

describe("AGENT_TEMPLATE_METADATA", () => {
  it("covers all 15 agent templates", () => {
    expect(Object.keys(AGENT_TEMPLATE_METADATA).length).toBe(15);
    for (const id of ALL_AGENT_TEMPLATES) {
      const m = AGENT_TEMPLATE_METADATA[id];
      expect(m, `missing template ${id}`).toBeDefined();
      expect(m.label.length).toBeGreaterThan(0);
      expect(m.summary.length).toBeGreaterThan(0);
      expect(m.whenToUse.length).toBeGreaterThan(0);
    }
  });
});

describe("getStepMeta", () => {
  it("returns known metadata", () => {
    expect(getStepMeta("llm").label).toBe("LLM");
  });

  it("returns a sensible fallback for unknown types", () => {
    const m = getStepMeta("totally_made_up");
    expect(m.type).toBe("totally_made_up");
    expect(m.label).toBe("Totally made up");
    expect(m.summary).toContain("totally_made_up");
    expect(m.iconKey).toBe("totally_made_up");
  });

  it("does not throw on empty input", () => {
    expect(() => getStepMeta("")).not.toThrow();
  });
});

describe("getAgentTemplateMeta", () => {
  it("returns known template metadata", () => {
    expect(getAgentTemplateMeta("coder").label).toBe("Coder");
  });

  it("falls back for unknown templates", () => {
    const m = getAgentTemplateMeta("ghost_writer");
    expect(m.id).toBe("ghost_writer");
    expect(m.label).toBe("Ghost writer");
  });
});
