import {
  TrendingUp,
  Megaphone,
  Headphones,
  Code2,
  FileText,
  Brain,
  type LucideIcon,
} from "lucide-react";

export interface TemplatePack {
  id: string;
  name: string;
  description: string;
  tagline: string;
  icon: LucideIcon;
  color: { bg: string; text: string; border: string };
  tools: string[];
  templates: string[];
  featured: boolean;
  setupHint: string;
}

export const TEMPLATE_PACKS: TemplatePack[] = [
  {
    id: "sales_crm",
    name: "Sales & CRM",
    description: "Lead scoring, pipeline management, proposals, and CRM enrichment workflows",
    tagline: "Everything your revenue team needs, from lead capture to closed-won.",
    icon: TrendingUp,
    color: { bg: "bg-amber-500/15", text: "text-amber-500", border: "border-amber-500/30" },
    tools: ["salesforce", "hubspot", "mail", "slack"],
    templates: [
      "lead-scoring",
      "lead-enrichment",
      "crm-contact-enrichment",
      "sales-pipeline-autopilot",
      "proposal-generator",
      "meeting-recap",
      "customer-churn-predictor",
    ],
    featured: true,
    setupHint: "Connect your Salesforce or HubSpot account in Settings > Integrations to unlock CRM workflows.",
  },
  {
    id: "marketing",
    name: "Marketing",
    description: "Content creation, SEO, email campaigns, and competitor analysis",
    tagline: "Turn content into campaigns and insights into action.",
    icon: Megaphone,
    color: { bg: "bg-violet-500/15", text: "text-violet-500", border: "border-violet-500/30" },
    tools: ["mail", "slack", "notion"],
    templates: [
      "blog-to-social",
      "seo-content-writer",
      "email-campaign-generator",
      "ad-copy-generator",
      "competitor-analysis",
    ],
    featured: false,
    setupHint: "Set up your email and Slack integrations for automated content distribution.",
  },
  {
    id: "support",
    name: "Customer Support",
    description: "Ticket triage, sentiment analysis, and customer health monitoring",
    tagline: "Triage faster, respond smarter, keep customers happy.",
    icon: Headphones,
    color: { bg: "bg-rose-500/15", text: "text-rose-500", border: "border-rose-500/30" },
    tools: ["zendesk", "slack"],
    templates: [
      "support-ticket-triage",
      "ticket-classifier",
      "customer-health-check",
      "review-sentiment",
      "sla-watchdog",
      "faq-generator",
    ],
    featured: false,
    setupHint: "Connect Zendesk in Settings > Integrations to enable automated ticket handling.",
  },
  {
    id: "engineering",
    name: "Engineering",
    description: "Release notes, sprint standups, Jira triage, and code workflows",
    tagline: "Automate the toil so your team ships faster.",
    icon: Code2,
    color: { bg: "bg-blue-500/15", text: "text-blue-500", border: "border-blue-500/30" },
    tools: ["github", "jira", "slack"],
    templates: [
      "sprint-standup",
      "slack-standup-summary",
      "release-notes-generator",
      "jira-issue-triage",
      "data-extractor",
      "api-docs-generator",
    ],
    featured: false,
    setupHint: "Connect GitHub and Jira in Settings > Integrations to power engineering workflows.",
  },
  {
    id: "hr_legal",
    name: "HR & Legal",
    description: "Job descriptions, resume screening, and contract review",
    tagline: "From hiring to compliance, handled by AI.",
    icon: FileText,
    color: { bg: "bg-emerald-500/15", text: "text-emerald-500", border: "border-emerald-500/30" },
    tools: ["mail", "notion"],
    templates: [
      "job-description-generator",
      "resume-screener",
      "contract-review",
      "compliance-checker",
      "employee-onboarding",
    ],
    featured: false,
    setupHint: "Set up Notion and email integrations for document and communication workflows.",
  },
  {
    id: "general_ai",
    name: "General AI",
    description: "Summarization, translation, research, and reasoning workflows",
    tagline: "General-purpose AI building blocks for any workflow.",
    icon: Brain,
    color: { bg: "bg-gray-500/15", text: "text-gray-400", border: "border-gray-500/30" },
    tools: [],
    templates: [
      "research-agent",
      "text-summarizer",
      "language-translator",
      "chain-of-thought-solver",
      "review-and-approve",
      "pdf-summary",
    ],
    featured: false,
    setupHint: "These templates work out of the box - no external integrations required.",
  },
];

/** Map tags/keywords to pack IDs for fallback resolution. */
const TAG_TO_PACK: Record<string, string> = {
  Sales: "sales_crm",
  CRM: "sales_crm",
  "Lead-Gen": "sales_crm",
  Pipeline: "sales_crm",
  Proposal: "sales_crm",
  Marketing: "marketing",
  SEO: "marketing",
  Content: "marketing",
  Social: "marketing",
  Email: "marketing",
  Campaign: "marketing",
  Advertising: "marketing",
  Copywriting: "marketing",
  Support: "support",
  Sentiment: "support",
  Classification: "support",
  Engineering: "engineering",
  Product: "engineering",
  Documentation: "engineering",
  Data: "engineering",
  HR: "hr_legal",
  Legal: "hr_legal",
  Recruiting: "hr_legal",
  Compliance: "hr_legal",
  NLP: "general_ai",
  Translation: "general_ai",
  Research: "general_ai",
  Reasoning: "general_ai",
  Chain: "general_ai",
  "Human-in-loop": "general_ai",
  "Multi-agent": "general_ai",
};

/**
 * Resolve a template's pack ID from its explicit category or tags.
 * Returns the pack ID string.
 */
export function resolveCategory(category?: string | null, tags?: string[]): string {
  if (category) return category;
  if (tags) {
    for (const tag of tags) {
      const pack = TAG_TO_PACK[tag];
      if (pack) return pack;
    }
  }
  return "general_ai";
}

/** Look up a pack by its ID. */
export function getPackById(id: string): TemplatePack | undefined {
  return TEMPLATE_PACKS.find((p) => p.id === id);
}

/** Get the featured pack. */
export function getFeaturedPack(): TemplatePack {
  return TEMPLATE_PACKS.find((p) => p.featured) || TEMPLATE_PACKS[0];
}
