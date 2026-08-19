/**
 * stepMetadata.ts — single source of truth for Workflow Builder step types.
 *
 * Every step type the builder supports (StepConfigPanel's `StepType` union plus
 * the palette in WorkflowBuilder) has one `StepMeta` entry describing it in
 * plain English: what it does, when to reach for it, a concrete example, and an
 * optional cost/performance hint. The `iconKey` reuses the existing lucide icon
 * mapping (see STEP_TYPE_ICONS in StepNode.tsx) so callers can resolve an icon
 * without duplicating the map.
 *
 * Consumers: hover cards, the step palette, the config panel header, validation
 * hints. This module is framework-agnostic (no React imports) and side-effect
 * free, so it is safe to import from anywhere.
 */

import type { StepType } from "@/components/workflows/StepConfigPanel";

/** High-level grouping — matches the palette categories in WorkflowBuilder. */
export type StepCategory =
  | "AI"
  | "Control Flow"
  | "Integration"
  | "Output"
  | "Agents";

export interface StepMeta {
  /** The step type discriminator (matches StepConfig.stepType). */
  type: StepType;
  /** Short human label as shown in the palette / config panel. */
  label: string;
  /** Which palette group this step belongs to. */
  category: StepCategory;
  /** One plain-English sentence: what this step does. */
  summary: string;
  /** One short decision cue, e.g. "Use when you need…". */
  whenToUse: string;
  /** One concrete example of the step in action. */
  example: string;
  /** Optional cost / performance hint, e.g. "$0 — runs locally". */
  costNote?: string;
  /**
   * Key into the existing lucide icon map (STEP_TYPE_ICONS in StepNode.tsx).
   * Falls back to the type itself when no dedicated icon exists.
   */
  iconKey: string;
  /** Optional link to deeper docs. */
  docHref?: string;
}

/**
 * STEP_TYPE_METADATA — every supported StepType keyed by its discriminator.
 * Text is derived from the inline descriptions in StepConfigPanel where those
 * exist; the rest are written to the same factual, concise standard.
 */
export const STEP_TYPE_METADATA: Record<StepType, StepMeta> = {
  standard: {
    type: "standard",
    label: "Agent",
    category: "AI",
    summary:
      "A full agent with a sandbox — runs a multi-turn conversation and can use tools.",
    whenToUse:
      "Use when the task needs reasoning over several turns, files, or tools.",
    example: 'Research a company, browse its site, and write a one-page brief.',
    costNote: "LLM agent — costs per token, can run many turns",
    iconKey: "standard",
  },
  llm: {
    type: "llm",
    label: "LLM",
    category: "AI",
    summary:
      "A single LLM API call — no sandbox and no tools, so it is fast and cheap.",
    whenToUse:
      "Use when one prompt-and-response is enough (summarize, rewrite, extract).",
    example: 'Summarize {input.text} into three bullet points.',
    costNote: "LLM call — costs per token, single round-trip",
    iconKey: "llm",
  },
  classify: {
    type: "classify",
    label: "Classify",
    category: "AI",
    summary:
      "Uses an LLM to sort the input into one of your categories and route to that branch.",
    whenToUse: "Use when you need to branch on what the content is about.",
    example: 'Label a support ticket as billing, bug, or feature and route it.',
    costNote: "LLM call — costs per token",
    iconKey: "classify",
  },
  condition: {
    type: "condition",
    label: "If/Else",
    category: "Control Flow",
    summary: "Branches the workflow based on an expression that evaluates true or false.",
    whenToUse: "Use when a value decides which path to take.",
    example: 'If {steps.score.output} > 0.8 go to "publish", else "review".',
    costNote: "no model — free/instant",
    iconKey: "condition",
  },
  loop: {
    type: "loop",
    label: "Loop",
    category: "Control Flow",
    summary: "Iterates over a list, running its sub-steps once for each item.",
    whenToUse: "Use when you need to repeat the same work for every item.",
    example: 'For each URL in {input.urls}, fetch and summarize the page.',
    costNote: "cost depends on the steps inside the loop",
    iconKey: "loop",
  },
  race: {
    type: "race",
    label: "Race",
    category: "Control Flow",
    summary: "Runs several branches in parallel — the first valid result wins.",
    whenToUse: "Use when you want the fastest or first acceptable answer.",
    example:
      "Try GPT-4 and Claude in parallel, take whichever finishes first.",
    costNote: "runs all branches — you pay for every branch started",
    iconKey: "race",
  },
  gate: {
    type: "gate",
    label: "Gate",
    category: "Control Flow",
    summary:
      "A multi-strategy approval gate that can use an LLM eval, a human, or a timeout.",
    whenToUse: "Use when a step must be approved before the workflow continues.",
    example: "Pause until a reviewer approves, or auto-approve after 24h.",
    costNote: "LLM eval strategy costs per token; human/timeout are free",
    iconKey: "gate",
  },
  approval: {
    type: "approval",
    label: "Approval",
    category: "Control Flow",
    summary: "Pauses the workflow until a person approves or rejects it.",
    whenToUse: "Use when a human must sign off before continuing.",
    example: "Hold the published draft until an editor clicks Approve.",
    costNote: "no model — free, waits for a human",
    iconKey: "approval",
  },
  http: {
    type: "http",
    label: "HTTP",
    category: "Integration",
    summary: "Makes a direct HTTP request to an API — no LLM is involved.",
    whenToUse: "Use when you need to call or POST to an external service.",
    example: "POST the result to a Zapier webhook.",
    costNote: "$0 — no LLM, just a network call",
    iconKey: "http",
  },
  code: {
    type: "code",
    label: "Code",
    category: "Integration",
    summary: "Runs inline Python code against the workflow data.",
    whenToUse: "Use when you need custom logic, parsing, or math.",
    example: "Parse the JSON response and compute an average.",
    costNote: "$0 — runs locally, no LLM",
    iconKey: "code",
  },
  delegate: {
    type: "delegate",
    label: "Delegate",
    category: "Integration",
    summary: "Hands off work to another workflow as a sub-task.",
    whenToUse: "Use when reusable logic already lives in another workflow.",
    example: 'Delegate "enrich-lead" with the new contact, wait for its result.',
    costNote: "cost depends on the delegated workflow",
    iconKey: "delegate",
  },
  browser: {
    type: "browser",
    label: "Browser",
    category: "Integration",
    summary:
      "Automates a browser via Playwright selectors or Computer Use visual AI.",
    whenToUse: "Use when a task needs to click through a real website.",
    example: "Log in, navigate to billing, and download the invoice PDF.",
    costNote: "Computer Use mode costs per token; selector mode is cheaper",
    iconKey: "browser",
  },
  parse: {
    type: "parse",
    label: "Parse",
    category: "Integration",
    summary: "Extracts text from PDF, DOCX, XLSX, PPTX, or CSV files.",
    whenToUse: "Use when you need the text content out of a document.",
    example: "Pull the plain text from an uploaded contract PDF.",
    costNote: "$0 — no LLM, local extraction",
    iconKey: "parse",
  },
  sub_workflow: {
    type: "sub_workflow",
    label: "Sub-workflow",
    category: "Integration",
    summary: "Runs another workflow inline as a nested step.",
    whenToUse: "Use to compose a larger workflow out of smaller ones.",
    example: 'Run the "qa-checks" sub-workflow before publishing.',
    costNote: "cost depends on the nested workflow",
    iconKey: "sub_workflow",
  },
  openclaw: {
    type: "openclaw",
    label: "OpenClaw",
    category: "Integration",
    summary: "Runs an OpenClaw browser/scraping action against a target site.",
    whenToUse: "Use when you need OpenClaw-driven extraction or automation.",
    example: "Scrape product prices from a category page.",
    costNote: "cost depends on the OpenClaw action",
    iconKey: "openclaw",
  },
  acp: {
    type: "acp",
    label: "ACP Agent",
    category: "Agents",
    summary:
      "Drives an external agent harness (Claude Code, Codex, Gemini CLI, goose) over the Agent Client Protocol.",
    whenToUse:
      "Use when the work needs a real coding agent with filesystem access on a checked-out repo.",
    example: "Implement a brief in a git worktree, then verify the diff.",
    costNote:
      "billed by the external harness \u2014 max_cost_usd is advisory, set cost_per_call to make it count",
    iconKey: "agent",
    docHref: "https://github.com/gizmax/Sandcastle/blob/main/docs/acp.md",
  },
  composio: {
    type: "composio",
    label: "Composio",
    category: "Integration",
    summary: "Calls a Composio-connected tool or SaaS action (e.g. GitHub, Slack).",
    whenToUse: "Use when you need a ready-made integration to a third-party app.",
    example: "Create a GitHub issue from the workflow output.",
    costNote: "$0 from the LLM side — third-party API limits may apply",
    iconKey: "composio",
  },
  sensor: {
    type: "sensor",
    label: "Sensor",
    category: "Output",
    summary: "Polls an external URL until a condition is met.",
    whenToUse: "Use when you must wait for an external event or state change.",
    example: "Poll a status endpoint until the job reports done.",
    costNote: "$0 — no LLM, periodic polling",
    iconKey: "sensor",
  },
  transform: {
    type: "transform",
    label: "Transform",
    category: "Output",
    summary: "Reshapes data with a template — no LLM is involved.",
    whenToUse: "Use when you only need to format or restructure values.",
    example: 'Build a Markdown table from {steps.rows.output}.',
    costNote: "$0 — template only, no LLM",
    iconKey: "transform",
  },
  notify: {
    type: "notify",
    label: "Notify",
    category: "Output",
    summary: "Sends a notification via Slack, Teams, Gmail, or a webhook.",
    whenToUse: "Use when someone or some channel should be told the result.",
    example: "Post the summary to the #releases Slack channel.",
    costNote: "$0 — no LLM, just a delivery call",
    iconKey: "notify",
  },
  agent: {
    type: "agent",
    label: "Agent",
    category: "Agents",
    summary:
      "A universal agent that auto-selects its runtime (Anthropic cloud or local Ollama).",
    whenToUse:
      "Use when you want an agent and let the platform pick where it runs.",
    example: "Run a researcher template that picks the best available runtime.",
    costNote: "cloud runtime costs per token; local runtime is $0",
    iconKey: "standard",
  },
  "managed-agent": {
    type: "managed-agent",
    label: "Managed Agent",
    category: "Agents",
    summary:
      "An Anthropic Managed Agent running in cloud containers with bash, web, and files.",
    whenToUse:
      "Use when you need a hosted agent with a full toolset and no local setup.",
    example: "Spin up a cloud agent to clone a repo, run tests, and report back.",
    costNote: "LLM agent — costs per token, hosted containers",
    iconKey: "standard",
  },
  report: {
    type: "report",
    label: "Report",
    category: "Output",
    summary: "Compiles workflow results into a formatted report or document.",
    whenToUse: "Use when the workflow should end with a shareable report.",
    example: "Render the findings as a PDF report and attach it to the run.",
    costNote: "$0 — formatting only, no LLM",
    iconKey: "report",
  },
};

/** All step types covered by STEP_TYPE_METADATA, in palette-ish order. */
export const STEP_TYPES: StepType[] = Object.keys(
  STEP_TYPE_METADATA,
) as StepType[];

// ── Agent templates ──────────────────────────────────────────────────────────

export interface AgentTemplateMeta {
  /** Template id (matches AGENT_TEMPLATES[].id in StepConfigPanel). */
  id: string;
  /** Human label. */
  label: string;
  /** One plain-English sentence: what this agent persona does. */
  summary: string;
  /** One short decision cue. */
  whenToUse: string;
}

/**
 * AGENT_TEMPLATE_METADATA — describes each of the 15 agent personas the builder
 * exposes (see AGENT_TEMPLATES in StepConfigPanel). Keyed by template id.
 */
export const AGENT_TEMPLATE_METADATA: Record<string, AgentTemplateMeta> = {
  researcher: {
    id: "researcher",
    label: "Researcher",
    summary: "Gathers, reads, and synthesizes information from many sources.",
    whenToUse: "Use when you need facts, context, or a literature scan.",
  },
  coder: {
    id: "coder",
    label: "Coder",
    summary: "Writes, edits, and refactors code to meet a spec.",
    whenToUse: "Use when the task is to produce or change source code.",
  },
  analyst: {
    id: "analyst",
    label: "Analyst",
    summary: "Examines data, finds patterns, and explains what they mean.",
    whenToUse: "Use when you need insight or interpretation from data.",
  },
  writer: {
    id: "writer",
    label: "Writer",
    summary: "Drafts and polishes prose for a target audience and tone.",
    whenToUse: "Use when you need readable, well-structured written content.",
  },
  reviewer: {
    id: "reviewer",
    label: "Reviewer",
    summary: "Critiques work for correctness, quality, and risks.",
    whenToUse: "Use when output needs a quality or correctness check.",
  },
  scraper: {
    id: "scraper",
    label: "Scraper",
    summary: "Extracts structured data from web pages and documents.",
    whenToUse: "Use when you need data pulled out of sites or files.",
  },
  tester: {
    id: "tester",
    label: "Tester",
    summary: "Designs and runs tests, then reports what passed or failed.",
    whenToUse: "Use when you need verification or test coverage.",
  },
  devops: {
    id: "devops",
    label: "DevOps",
    summary: "Handles builds, deploys, infra, and CI/CD tasks.",
    whenToUse: "Use when the task touches deployment or infrastructure.",
  },
  translator: {
    id: "translator",
    label: "Translator",
    summary: "Translates text between languages while keeping meaning and tone.",
    whenToUse: "Use when content must be rendered in another language.",
  },
  designer: {
    id: "designer",
    label: "Designer",
    summary: "Produces UI/UX and visual design guidance or assets.",
    whenToUse: "Use when the task needs design thinking or layout.",
  },
  sql_expert: {
    id: "sql_expert",
    label: "SQL Expert",
    summary: "Writes and optimizes SQL queries against your schema.",
    whenToUse: "Use when you need data pulled or transformed via SQL.",
  },
  seo_specialist: {
    id: "seo_specialist",
    label: "SEO Specialist",
    summary: "Optimizes content and metadata for search visibility.",
    whenToUse: "Use when content should rank better in search.",
  },
  financial_analyst: {
    id: "financial_analyst",
    label: "Financial Analyst",
    summary: "Builds models and interprets financial data and metrics.",
    whenToUse: "Use when the task involves finance, valuation, or forecasting.",
  },
  legal_analyst: {
    id: "legal_analyst",
    label: "Legal Analyst",
    summary: "Reviews documents for legal terms, risks, and obligations.",
    whenToUse: "Use when you need contract or compliance analysis.",
  },
  project_manager: {
    id: "project_manager",
    label: "Project Manager",
    summary: "Plans, sequences, and tracks work into actionable tasks.",
    whenToUse: "Use when you need a plan, breakdown, or status synthesis.",
  },
};

/** All agent template ids covered by AGENT_TEMPLATE_METADATA. */
export const AGENT_TEMPLATE_IDS: string[] = Object.keys(AGENT_TEMPLATE_METADATA);

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * getStepMeta — returns the metadata for a step type, or a sensible generic
 * fallback for unknown/legacy types so callers never have to null-check.
 */
export function getStepMeta(type: string): StepMeta {
  const known = STEP_TYPE_METADATA[type as StepType];
  if (known) return known;
  return {
    type: type as StepType,
    label: type
      ? type.charAt(0).toUpperCase() + type.slice(1).replace(/[_-]/g, " ")
      : "Step",
    category: "Integration",
    summary: `A ${type || "custom"} step.`,
    whenToUse: "Use when this step type fits your workflow.",
    example: `Run a ${type || "custom"} step.`,
    iconKey: type || "standard",
  };
}

/**
 * getAgentTemplateMeta — metadata for an agent template id, or a generic
 * fallback for unknown templates.
 */
export function getAgentTemplateMeta(id: string): AgentTemplateMeta {
  const known = AGENT_TEMPLATE_METADATA[id];
  if (known) return known;
  return {
    id,
    label: id
      ? id.charAt(0).toUpperCase() + id.slice(1).replace(/[_-]/g, " ")
      : "Agent",
    summary: "A custom agent persona.",
    whenToUse: "Use when this agent fits your task.",
  };
}
