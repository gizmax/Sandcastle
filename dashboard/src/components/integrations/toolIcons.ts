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
  Table2,
  LayoutGrid,
  Target,
  Gamepad2,
  Brain,
  Cpu,
  Box,
  Server,
  DatabaseZap,
  Crosshair,
  MailCheck,
  Rocket,
  CloudCog,
  Flame,
  Search,
  AudioLines,
  Zap,
  ShoppingCart,
  Receipt,
  Calendar,
  Figma,
  Activity,
  DollarSign,
  FileSignature,
  AlertTriangle,
  Link2,
  UserCheck,
  FolderOpen,
  Terminal,
  Code2,
  Globe,
  Landmark,
  Briefcase,
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
  // New integrations
  "google-sheets": Table2,
  airtable: LayoutGrid,
  linear: Target,
  discord: Gamepad2,
  openai: Brain,
  anthropic: Cpu,
  "aws-s3": Box,
  redis: Server,
  supabase: DatabaseZap,
  pinecone: Crosshair,
  resend: MailCheck,
  vercel: Rocket,
  "cloudflare-workers": CloudCog,
  firecrawl: Flame,
  tavily: Search,
  elevenlabs: AudioLines,
  zapier: Zap,
  shopify: ShoppingCart,
  quickbooks: Receipt,
  calendly: Calendar,
  whatsapp: MessageCircle,
  figma: Figma,
  datadog: Activity,
  plaid: DollarSign,
  docusign: FileSignature,
  pagerduty: AlertTriangle,
  "mcp-bridge": Link2,
  "human-input": UserCheck,
  filesystem: FolderOpen,
  shell: Terminal,
  "python-runtime": Code2,
  "code-interpreter": Code2,
  browser: Globe,
  helios: Landmark,
  abra: Briefcase,
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
  ai: { bg: "bg-pink-500/15", text: "text-pink-500" },
  devops: { bg: "bg-cyan-500/15", text: "text-cyan-500" },
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
  ai: "AI",
  devops: "DevOps",
};

/** Proper display names for tools (no CSS capitalize needed). */
export const TOOL_DISPLAY_NAMES: Record<string, string> = {
  // Communication
  slack: "Slack",
  gmail: "Gmail",
  teams: "Microsoft Teams",
  discord: "Discord",
  twilio: "Twilio",
  sendgrid: "SendGrid",
  intercom: "Intercom",
  resend: "Resend",
  whatsapp: "WhatsApp",
  // Project Management
  jira: "Jira",
  github: "GitHub",
  notion: "Notion",
  linear: "Linear",
  servicenow: "ServiceNow",
  // CRM
  hubspot: "HubSpot",
  salesforce: "Salesforce",
  zendesk: "Zendesk",
  // Data
  gdrive: "Google Drive",
  "google-sheets": "Google Sheets",
  postgresql: "PostgreSQL",
  database: "Database",
  snowflake: "Snowflake",
  mongodb: "MongoDB",
  airtable: "Airtable",
  "aws-s3": "AWS S3",
  redis: "Redis",
  supabase: "Supabase",
  // AI
  openai: "OpenAI",
  anthropic: "Anthropic",
  pinecone: "Pinecone",
  firecrawl: "Firecrawl",
  tavily: "Tavily",
  elevenlabs: "ElevenLabs",
  "mcp-bridge": "MCP Bridge",
  "code-interpreter": "Code Interpreter",
  // ERP
  sap: "SAP",
  shopify: "Shopify",
  quickbooks: "QuickBooks",
  helios: "Helios",
  abra: "Abra",
  // DevOps
  vercel: "Vercel",
  "cloudflare-workers": "Cloudflare Workers",
  datadog: "Datadog",
  pagerduty: "PagerDuty",
  // Payments
  stripe: "Stripe",
  plaid: "Plaid",
  // General
  webhook: "Webhook",
  browser: "Browser",
  "human-input": "Human Input",
  filesystem: "Filesystem",
  shell: "Shell",
  "python-runtime": "Python Runtime",
  zapier: "Zapier",
  calendly: "Calendly",
  figma: "Figma",
  docusign: "DocuSign",
  mail: "Mail",
};

/** Human-readable labels for credential env vars. */
export const CREDENTIAL_LABELS: Record<string, { label: string; hint?: string }> = {
  // Slack
  TOOL_SLACK_BOT_TOKEN: { label: "Bot Token", hint: "xoxb-..." },
  // Gmail / SMTP
  TOOL_SMTP_HOST: { label: "SMTP Host", hint: "smtp.gmail.com" },
  TOOL_SMTP_PORT: { label: "SMTP Port", hint: "587" },
  TOOL_SMTP_USER: { label: "SMTP Username" },
  TOOL_SMTP_PASSWORD: { label: "SMTP Password" },
  // Teams
  TOOL_TEAMS_WEBHOOK_URL: { label: "Webhook URL", hint: "https://..." },
  // Discord
  TOOL_DISCORD_BOT_TOKEN: { label: "Bot Token" },
  TOOL_DISCORD_WEBHOOK_URL: { label: "Webhook URL", hint: "https://discord.com/api/webhooks/..." },
  // Twilio
  TOOL_TWILIO_ACCOUNT_SID: { label: "Account SID", hint: "AC..." },
  TOOL_TWILIO_AUTH_TOKEN: { label: "Auth Token" },
  TOOL_TWILIO_FROM_NUMBER: { label: "From Number", hint: "+1234567890" },
  // SendGrid
  TOOL_SENDGRID_API_KEY: { label: "API Key", hint: "SG...." },
  TOOL_SENDGRID_FROM_EMAIL: { label: "From Email", hint: "noreply@example.com" },
  // Intercom
  TOOL_INTERCOM_ACCESS_TOKEN: { label: "Access Token" },
  // Resend
  TOOL_RESEND_API_KEY: { label: "API Key", hint: "re_..." },
  // WhatsApp
  TOOL_WHATSAPP_TOKEN: { label: "Access Token" },
  TOOL_WHATSAPP_PHONE_ID: { label: "Phone Number ID" },
  // Jira
  TOOL_JIRA_API_TOKEN: { label: "API Token" },
  TOOL_JIRA_BASE_URL: { label: "Instance URL", hint: "https://yourorg.atlassian.net" },
  TOOL_JIRA_EMAIL: { label: "Email" },
  // GitHub
  TOOL_GITHUB_TOKEN: { label: "Personal Access Token", hint: "ghp_..." },
  // Notion
  TOOL_NOTION_API_KEY: { label: "Integration Token", hint: "secret_..." },
  // Linear
  TOOL_LINEAR_API_KEY: { label: "API Key", hint: "lin_api_..." },
  // ServiceNow
  TOOL_SERVICENOW_INSTANCE: { label: "Instance URL", hint: "https://yourorg.service-now.com" },
  TOOL_SERVICENOW_USERNAME: { label: "Username" },
  TOOL_SERVICENOW_PASSWORD: { label: "Password" },
  // HubSpot
  TOOL_HUBSPOT_API_KEY: { label: "Private App Token" },
  // Salesforce
  TOOL_SALESFORCE_CLIENT_ID: { label: "Client ID" },
  TOOL_SALESFORCE_CLIENT_SECRET: { label: "Client Secret" },
  TOOL_SALESFORCE_REFRESH_TOKEN: { label: "Refresh Token" },
  TOOL_SALESFORCE_INSTANCE_URL: { label: "Instance URL", hint: "https://yourorg.my.salesforce.com" },
  // Zendesk
  TOOL_ZENDESK_SUBDOMAIN: { label: "Subdomain", hint: "yourorg" },
  TOOL_ZENDESK_EMAIL: { label: "Email" },
  TOOL_ZENDESK_API_TOKEN: { label: "API Token" },
  // Google Drive
  TOOL_GOOGLE_SERVICE_ACCOUNT: { label: "Service Account JSON" },
  // Google Sheets
  TOOL_GOOGLE_SHEETS_SERVICE_ACCOUNT: { label: "Service Account JSON" },
  TOOL_GOOGLE_SHEETS_SPREADSHEET_ID: { label: "Spreadsheet ID" },
  // PostgreSQL
  TOOL_POSTGRESQL_URL: { label: "Connection URL", hint: "postgresql://user:pass@host:5432/db" },
  // Snowflake
  TOOL_SNOWFLAKE_ACCOUNT: { label: "Account Identifier", hint: "orgname-accountname" },
  TOOL_SNOWFLAKE_USERNAME: { label: "Username" },
  TOOL_SNOWFLAKE_PASSWORD: { label: "Password" },
  TOOL_SNOWFLAKE_WAREHOUSE: { label: "Warehouse", hint: "COMPUTE_WH" },
  // MongoDB
  TOOL_MONGODB_URI: { label: "Connection URI", hint: "mongodb+srv://..." },
  TOOL_MONGODB_API_KEY: { label: "API Key" },
  // Airtable
  TOOL_AIRTABLE_API_KEY: { label: "Personal Access Token", hint: "pat..." },
  TOOL_AIRTABLE_BASE_ID: { label: "Base ID", hint: "app..." },
  // AWS S3
  TOOL_AWS_ACCESS_KEY_ID: { label: "Access Key ID", hint: "AKIA..." },
  TOOL_AWS_SECRET_ACCESS_KEY: { label: "Secret Access Key" },
  TOOL_AWS_REGION: { label: "Region", hint: "us-east-1" },
  TOOL_AWS_S3_BUCKET: { label: "S3 Bucket Name" },
  // Redis
  TOOL_REDIS_URL: { label: "Connection URL", hint: "redis://..." },
  TOOL_REDIS_TOKEN: { label: "Auth Token" },
  // Supabase
  TOOL_SUPABASE_URL: { label: "Project URL", hint: "https://xxx.supabase.co" },
  TOOL_SUPABASE_SERVICE_KEY: { label: "Service Role Key", hint: "eyJ..." },
  // OpenAI
  TOOL_OPENAI_API_KEY: { label: "API Key", hint: "sk-..." },
  // Anthropic
  TOOL_ANTHROPIC_API_KEY: { label: "API Key", hint: "sk-ant-..." },
  // Pinecone
  TOOL_PINECONE_API_KEY: { label: "API Key" },
  TOOL_PINECONE_INDEX_HOST: { label: "Index Host", hint: "https://index-xxx.svc.xxx.pinecone.io" },
  // Firecrawl
  TOOL_FIRECRAWL_API_KEY: { label: "API Key", hint: "fc-..." },
  // Tavily
  TOOL_TAVILY_API_KEY: { label: "API Key", hint: "tvly-..." },
  // ElevenLabs
  TOOL_ELEVENLABS_API_KEY: { label: "API Key" },
  // MCP Bridge
  TOOL_MCP_SERVER_URL: { label: "Server URL", hint: "http://localhost:3000" },
  // SAP
  TOOL_SAP_BASE_URL: { label: "Base URL" },
  TOOL_SAP_API_KEY: { label: "API Key" },
  // Shopify
  TOOL_SHOPIFY_STORE: { label: "Store Domain", hint: "yourstore.myshopify.com" },
  TOOL_SHOPIFY_ACCESS_TOKEN: { label: "Admin Access Token", hint: "shpat_..." },
  // QuickBooks
  TOOL_QUICKBOOKS_CLIENT_ID: { label: "Client ID" },
  TOOL_QUICKBOOKS_CLIENT_SECRET: { label: "Client Secret" },
  TOOL_QUICKBOOKS_REFRESH_TOKEN: { label: "Refresh Token" },
  TOOL_QUICKBOOKS_REALM_ID: { label: "Realm ID (Company ID)" },
  // Helios
  TOOL_HELIOS_BASE_URL: { label: "Base URL" },
  TOOL_HELIOS_API_KEY: { label: "API Key" },
  // Abra
  TOOL_ABRA_BASE_URL: { label: "Base URL" },
  TOOL_ABRA_USERNAME: { label: "Username" },
  TOOL_ABRA_PASSWORD: { label: "Password" },
  // Vercel
  TOOL_VERCEL_TOKEN: { label: "Personal Access Token" },
  // Cloudflare Workers
  TOOL_CLOUDFLARE_API_TOKEN: { label: "API Token" },
  TOOL_CLOUDFLARE_ACCOUNT_ID: { label: "Account ID" },
  // Datadog
  TOOL_DATADOG_API_KEY: { label: "API Key" },
  TOOL_DATADOG_APP_KEY: { label: "Application Key" },
  // PagerDuty
  TOOL_PAGERDUTY_API_KEY: { label: "API Key" },
  // Stripe
  TOOL_STRIPE_SECRET_KEY: { label: "Secret Key", hint: "sk_live_... or sk_test_..." },
  // Plaid
  TOOL_PLAID_CLIENT_ID: { label: "Client ID" },
  TOOL_PLAID_SECRET: { label: "Secret" },
  // Zapier
  TOOL_ZAPIER_WEBHOOK_URL: { label: "Webhook URL", hint: "https://hooks.zapier.com/..." },
  // Calendly
  TOOL_CALENDLY_API_KEY: { label: "Personal Access Token" },
  // Figma
  TOOL_FIGMA_ACCESS_TOKEN: { label: "Personal Access Token" },
  // DocuSign
  TOOL_DOCUSIGN_ACCESS_TOKEN: { label: "Access Token" },
  TOOL_DOCUSIGN_ACCOUNT_ID: { label: "Account ID" },
};

/** Where to get credentials for each tool. */
export const TOOL_SIGNUP_URLS: Record<string, { url: string; label: string }> = {
  slack: { url: "https://api.slack.com/apps", label: "Slack API Apps" },
  openai: { url: "https://platform.openai.com/api-keys", label: "OpenAI Platform" },
  anthropic: { url: "https://console.anthropic.com/", label: "Anthropic Console" },
  github: { url: "https://github.com/settings/tokens", label: "GitHub Settings" },
  stripe: { url: "https://dashboard.stripe.com/apikeys", label: "Stripe Dashboard" },
  supabase: { url: "https://supabase.com/dashboard", label: "Supabase Dashboard" },
  vercel: { url: "https://vercel.com/account/tokens", label: "Vercel Settings" },
  hubspot: { url: "https://developers.hubspot.com/", label: "HubSpot Developers" },
  jira: { url: "https://id.atlassian.com/manage-profile/security/api-tokens", label: "Atlassian API Tokens" },
  notion: { url: "https://www.notion.so/my-integrations", label: "Notion Integrations" },
  airtable: { url: "https://airtable.com/create/tokens", label: "Airtable Tokens" },
  sendgrid: { url: "https://app.sendgrid.com/settings/api_keys", label: "SendGrid Settings" },
  twilio: { url: "https://www.twilio.com/console", label: "Twilio Console" },
  pinecone: { url: "https://app.pinecone.io/", label: "Pinecone Console" },
  datadog: { url: "https://app.datadoghq.com/organization-settings/api-keys", label: "Datadog Settings" },
  linear: { url: "https://linear.app/settings/api", label: "Linear Settings" },
  discord: { url: "https://discord.com/developers/applications", label: "Discord Developer Portal" },
  elevenlabs: { url: "https://elevenlabs.io/app/settings/api-keys", label: "ElevenLabs Settings" },
  firecrawl: { url: "https://firecrawl.dev/app/api-keys", label: "Firecrawl Dashboard" },
  tavily: { url: "https://tavily.com/#api", label: "Tavily API" },
  resend: { url: "https://resend.com/api-keys", label: "Resend Dashboard" },
  plaid: { url: "https://dashboard.plaid.com/team/keys", label: "Plaid Dashboard" },
  mongodb: { url: "https://cloud.mongodb.com/", label: "MongoDB Atlas" },
  shopify: { url: "https://partners.shopify.com/", label: "Shopify Partners" },
  pagerduty: { url: "https://support.pagerduty.com/main/docs/api-access-keys", label: "PagerDuty Docs" },
  calendly: { url: "https://calendly.com/integrations/api_webhooks", label: "Calendly Integrations" },
  figma: { url: "https://www.figma.com/developers/api#access-tokens", label: "Figma Developers" },
  docusign: { url: "https://developers.docusign.com/", label: "DocuSign Developers" },
  zapier: { url: "https://zapier.com/app/developer", label: "Zapier Developer" },
  salesforce: { url: "https://developer.salesforce.com/", label: "Salesforce Developers" },
  zendesk: { url: "https://developer.zendesk.com/", label: "Zendesk Developers" },
  intercom: { url: "https://developers.intercom.com/", label: "Intercom Developers" },
  "google-sheets": { url: "https://console.cloud.google.com/apis/credentials", label: "Google Cloud Console" },
  gdrive: { url: "https://console.cloud.google.com/apis/credentials", label: "Google Cloud Console" },
  "cloudflare-workers": { url: "https://dash.cloudflare.com/profile/api-tokens", label: "Cloudflare Dashboard" },
  quickbooks: { url: "https://developer.intuit.com/app/developer/homepage", label: "Intuit Developer" },
  snowflake: { url: "https://app.snowflake.com/", label: "Snowflake Console" },
  redis: { url: "https://app.redislabs.com/", label: "Redis Cloud" },
  postgresql: { url: "https://neon.tech/", label: "Neon (Serverless Postgres)" },
};
