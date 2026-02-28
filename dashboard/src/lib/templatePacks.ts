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
      "Lead Scoring",
      "Lead Enrichment",
      "CRM Contact Enrichment",
      "Sales Pipeline Autopilot",
      "Proposal Generator",
      "Meeting Recap",
      "Customer Churn Predictor",
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
      "Blog to Social Media",
      "SEO Content Writer",
      "Email Campaign Generator",
      "Ad Copy Generator",
      "Competitor Analysis",
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
      "Support Ticket Triage",
      "Ticket Classifier",
      "Customer Health Check",
      "Review Sentiment",
      "SLA Watchdog",
      "FAQ Generator",
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
      "Sprint Standup",
      "Slack Standup Summary",
      "Release Notes Generator",
      "Jira Issue Triage",
      "Data Extractor",
      "API Docs Generator",
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
      "Job Description Generator",
      "Resume Screener",
      "Contract Review",
      "Compliance Checker",
      "Employee Onboarding",
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
      "Research Agent",
      "Text Summarizer",
      "Language Translator",
      "Chain of Thought Solver",
      "Review and Approve",
      "PDF Summary",
    ],
    featured: false,
    setupHint: "These templates work out of the box - no external integrations required.",
  },
];

/** Map tags/keywords to pack IDs for fallback resolution. */
const TAG_TO_PACK: Record<string, string> = {
  // Sales & CRM
  Sales: "sales_crm",
  CRM: "sales_crm",
  "Lead-Gen": "sales_crm",
  Pipeline: "sales_crm",
  Proposal: "sales_crm",
  Churn: "sales_crm",
  Retention: "sales_crm",
  "Customer-Success": "sales_crm",
  Forecasting: "sales_crm",
  Revenue: "sales_crm",
  // Marketing
  Marketing: "marketing",
  SEO: "marketing",
  Content: "marketing",
  Social: "marketing",
  Email: "marketing",
  Campaign: "marketing",
  Advertising: "marketing",
  Copywriting: "marketing",
  Competitive: "marketing",
  "Competitive-Intel": "marketing",
  Strategy: "marketing",
  "Market-Research": "marketing",
  "Market-Entry": "marketing",
  Trends: "marketing",
  // Support
  Support: "support",
  Sentiment: "support",
  Classification: "support",
  Triage: "support",
  Zendesk: "support",
  // Engineering
  Engineering: "engineering",
  Product: "engineering",
  Documentation: "engineering",
  Data: "engineering",
  DevOps: "engineering",
  Security: "engineering",
  "AI-Safety": "engineering",
  SRE: "engineering",
  RAG: "engineering",
  Embeddings: "engineering",
  "Knowledge-Base": "engineering",
  Monitoring: "engineering",
  "CI-CD": "engineering",
  // HR & Legal
  HR: "hr_legal",
  Legal: "hr_legal",
  Recruiting: "hr_legal",
  Compliance: "hr_legal",
  Negotiation: "hr_legal",
  Procurement: "hr_legal",
  // General AI
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
