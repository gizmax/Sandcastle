import {
  MessageSquare,
  Mail,
  Users,
  GitBranch,
  BookOpen,
  BarChart3,
  Cloud,
  Headphones,
  HardDrive,
  Database,
  Webhook,
  Building2,
  Wrench,
  Snowflake,
  CircleDot,
  CreditCard,
  Phone,
  Send,
  MessageCircle,
  type LucideIcon,
} from "lucide-react";

/** Map tool `icon` string to a lucide-react component. */
export const TOOL_ICON_MAP: Record<string, LucideIcon> = {
  slack: MessageSquare,
  jira: BookOpen,
  github: GitBranch,
  mail: Mail,
  notion: BookOpen,
  hubspot: BarChart3,
  salesforce: Cloud,
  zendesk: Headphones,
  teams: Users,
  gdrive: HardDrive,
  database: Database,
  webhook: Webhook,
  sap: Building2,
  servicenow: Wrench,
  snowflake: Snowflake,
  mongodb: CircleDot,
  stripe: CreditCard,
  twilio: Phone,
  sendgrid: Send,
  intercom: MessageCircle,
};

/** Category -> Tailwind color classes (bg for icon wrapper, text for icon). */
export const CATEGORY_COLORS: Record<string, { bg: string; text: string }> = {
  communication: { bg: "bg-blue-500/15", text: "text-blue-500" },
  project_management: { bg: "bg-purple-500/15", text: "text-purple-500" },
  crm: { bg: "bg-amber-500/15", text: "text-amber-500" },
  data: { bg: "bg-emerald-500/15", text: "text-emerald-500" },
  erp: { bg: "bg-orange-500/15", text: "text-orange-500" },
  payments: { bg: "bg-violet-500/15", text: "text-violet-500" },
  general: { bg: "bg-gray-500/15", text: "text-gray-400" },
};

/** Human-readable category labels. */
export const CATEGORY_LABELS: Record<string, string> = {
  communication: "Communication",
  project_management: "Project Mgmt",
  crm: "CRM",
  data: "Data",
  erp: "ERP",
  payments: "Payments",
  general: "General",
};
