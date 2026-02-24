"""Tool Registry - definitions and metadata for all available connectors.

Each tool has a name, description, available functions, parameter schemas,
and required credential environment variable names. The registry is the
single source of truth used by the YAML validator, credential resolver,
loader, and dashboard UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolFunction:
    """A single callable function within a tool."""

    name: str
    description: str
    parameters: dict  # JSON Schema for function parameters


@dataclass(frozen=True)
class ToolDefinition:
    """Complete definition of an external tool/connector."""

    name: str
    description: str
    category: str  # "communication", "project_management", "crm", "data", "general"
    functions: list[ToolFunction]
    credential_env_vars: list[str]  # Required env vars (e.g. ["TOOL_SLACK_BOT_TOKEN"])
    connector_file: str  # Filename in connectors/ (e.g. "slack.mjs")
    icon: str = ""  # Optional icon identifier for dashboard


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "slack": ToolDefinition(
        name="slack",
        description="Send and read messages in Slack channels",
        category="communication",
        functions=[
            ToolFunction(
                name="send_message",
                description="Send a message to a Slack channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel name or ID (e.g. '#general')"},
                        "text": {"type": "string", "description": "Message text (supports Slack mrkdwn)"},
                    },
                    "required": ["channel", "text"],
                },
            ),
            ToolFunction(
                name="list_channels",
                description="List available Slack channels",
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Max channels to return", "default": 100},
                    },
                },
            ),
            ToolFunction(
                name="get_messages",
                description="Get recent messages from a channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string", "description": "Channel name or ID"},
                        "limit": {"type": "integer", "description": "Max messages to return", "default": 20},
                    },
                    "required": ["channel"],
                },
            ),
        ],
        credential_env_vars=["TOOL_SLACK_BOT_TOKEN"],
        connector_file="slack.mjs",
        icon="slack",
    ),
    "jira": ToolDefinition(
        name="jira",
        description="Create, search, and manage Jira issues",
        category="project_management",
        functions=[
            ToolFunction(
                name="create_issue",
                description="Create a new Jira issue",
                parameters={
                    "type": "object",
                    "properties": {
                        "project": {"type": "string", "description": "Project key (e.g. 'PROJ')"},
                        "summary": {"type": "string", "description": "Issue title"},
                        "description": {"type": "string", "description": "Issue description"},
                        "issue_type": {"type": "string", "description": "Issue type (Bug, Task, Story)", "default": "Task"},
                    },
                    "required": ["project", "summary"],
                },
            ),
            ToolFunction(
                name="get_issue",
                description="Get details of a Jira issue by key",
                parameters={
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Issue key (e.g. 'PROJ-123')"},
                    },
                    "required": ["issue_key"],
                },
            ),
            ToolFunction(
                name="search_issues",
                description="Search Jira issues using JQL",
                parameters={
                    "type": "object",
                    "properties": {
                        "jql": {"type": "string", "description": "JQL query string"},
                        "max_results": {"type": "integer", "description": "Max results", "default": 20},
                    },
                    "required": ["jql"],
                },
            ),
            ToolFunction(
                name="add_comment",
                description="Add a comment to a Jira issue",
                parameters={
                    "type": "object",
                    "properties": {
                        "issue_key": {"type": "string", "description": "Issue key"},
                        "body": {"type": "string", "description": "Comment text"},
                    },
                    "required": ["issue_key", "body"],
                },
            ),
        ],
        credential_env_vars=["TOOL_JIRA_API_TOKEN", "TOOL_JIRA_BASE_URL", "TOOL_JIRA_EMAIL"],
        connector_file="jira.mjs",
        icon="jira",
    ),
    "github": ToolDefinition(
        name="github",
        description="Create issues, PRs, and manage GitHub repositories",
        category="project_management",
        functions=[
            ToolFunction(
                name="create_issue",
                description="Create a new GitHub issue",
                parameters={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "Repository (owner/repo)"},
                        "title": {"type": "string", "description": "Issue title"},
                        "body": {"type": "string", "description": "Issue body (markdown)"},
                        "labels": {"type": "string", "description": "Comma-separated labels"},
                    },
                    "required": ["repo", "title"],
                },
            ),
            ToolFunction(
                name="get_issues",
                description="List issues from a GitHub repository",
                parameters={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "Repository (owner/repo)"},
                        "state": {"type": "string", "description": "Filter: open, closed, all", "default": "open"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                    "required": ["repo"],
                },
            ),
            ToolFunction(
                name="create_pr",
                description="Create a pull request",
                parameters={
                    "type": "object",
                    "properties": {
                        "repo": {"type": "string", "description": "Repository (owner/repo)"},
                        "title": {"type": "string", "description": "PR title"},
                        "body": {"type": "string", "description": "PR description"},
                        "head": {"type": "string", "description": "Source branch"},
                        "base": {"type": "string", "description": "Target branch", "default": "main"},
                    },
                    "required": ["repo", "title", "head"],
                },
            ),
        ],
        credential_env_vars=["TOOL_GITHUB_TOKEN"],
        connector_file="github.mjs",
        icon="github",
    ),
    "gmail": ToolDefinition(
        name="gmail",
        description="Send and search emails via SMTP/IMAP",
        category="communication",
        functions=[
            ToolFunction(
                name="send_email",
                description="Send an email",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "body": {"type": "string", "description": "Email body (plain text or HTML)"},
                        "html": {"type": "boolean", "description": "Whether body is HTML", "default": False},
                    },
                    "required": ["to", "subject", "body"],
                },
            ),
            ToolFunction(
                name="search_emails",
                description="Search emails by query",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {"type": "integer", "description": "Max results", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
        ],
        credential_env_vars=["TOOL_SMTP_HOST", "TOOL_SMTP_PORT", "TOOL_SMTP_USER", "TOOL_SMTP_PASSWORD"],
        connector_file="gmail.mjs",
        icon="mail",
    ),
    "notion": ToolDefinition(
        name="notion",
        description="Search, read, and create Notion pages and databases",
        category="project_management",
        functions=[
            ToolFunction(
                name="search",
                description="Search Notion pages and databases",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "filter_type": {"type": "string", "description": "Filter: page or database"},
                    },
                    "required": ["query"],
                },
            ),
            ToolFunction(
                name="get_page",
                description="Get a Notion page by ID",
                parameters={
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Notion page ID"},
                    },
                    "required": ["page_id"],
                },
            ),
            ToolFunction(
                name="create_page",
                description="Create a new Notion page",
                parameters={
                    "type": "object",
                    "properties": {
                        "parent_id": {"type": "string", "description": "Parent page or database ID"},
                        "title": {"type": "string", "description": "Page title"},
                        "content": {"type": "string", "description": "Page content (markdown)"},
                    },
                    "required": ["parent_id", "title"],
                },
            ),
        ],
        credential_env_vars=["TOOL_NOTION_API_KEY"],
        connector_file="notion.mjs",
        icon="notion",
    ),
    "hubspot": ToolDefinition(
        name="hubspot",
        description="Manage HubSpot contacts, companies, and deals",
        category="crm",
        functions=[
            ToolFunction(
                name="get_contacts",
                description="Search or list HubSpot contacts",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query (name or email)"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_contact",
                description="Create a new HubSpot contact",
                parameters={
                    "type": "object",
                    "properties": {
                        "email": {"type": "string", "description": "Contact email"},
                        "firstname": {"type": "string", "description": "First name"},
                        "lastname": {"type": "string", "description": "Last name"},
                        "company": {"type": "string", "description": "Company name"},
                    },
                    "required": ["email"],
                },
            ),
            ToolFunction(
                name="create_deal",
                description="Create a new HubSpot deal",
                parameters={
                    "type": "object",
                    "properties": {
                        "dealname": {"type": "string", "description": "Deal name"},
                        "amount": {"type": "number", "description": "Deal amount"},
                        "pipeline": {"type": "string", "description": "Pipeline name", "default": "default"},
                        "dealstage": {"type": "string", "description": "Deal stage"},
                    },
                    "required": ["dealname"],
                },
            ),
        ],
        credential_env_vars=["TOOL_HUBSPOT_API_KEY"],
        connector_file="hubspot.mjs",
        icon="hubspot",
    ),
    "salesforce": ToolDefinition(
        name="salesforce",
        description="Query and manage Salesforce records via SOQL",
        category="crm",
        functions=[
            ToolFunction(
                name="query",
                description="Execute a SOQL query",
                parameters={
                    "type": "object",
                    "properties": {
                        "soql": {"type": "string", "description": "SOQL query string"},
                    },
                    "required": ["soql"],
                },
            ),
            ToolFunction(
                name="create_record",
                description="Create a new Salesforce record",
                parameters={
                    "type": "object",
                    "properties": {
                        "sobject": {"type": "string", "description": "SObject type (Account, Contact, Lead, etc.)"},
                        "data": {"type": "object", "description": "Record field values"},
                    },
                    "required": ["sobject", "data"],
                },
            ),
            ToolFunction(
                name="update_record",
                description="Update an existing Salesforce record",
                parameters={
                    "type": "object",
                    "properties": {
                        "sobject": {"type": "string", "description": "SObject type"},
                        "record_id": {"type": "string", "description": "Record ID"},
                        "data": {"type": "object", "description": "Fields to update"},
                    },
                    "required": ["sobject", "record_id", "data"],
                },
            ),
        ],
        credential_env_vars=["TOOL_SALESFORCE_CLIENT_ID", "TOOL_SALESFORCE_CLIENT_SECRET", "TOOL_SALESFORCE_REFRESH_TOKEN", "TOOL_SALESFORCE_INSTANCE_URL"],
        connector_file="salesforce.mjs",
        icon="salesforce",
    ),
    "zendesk": ToolDefinition(
        name="zendesk",
        description="Create and manage Zendesk support tickets",
        category="crm",
        functions=[
            ToolFunction(
                name="create_ticket",
                description="Create a new support ticket",
                parameters={
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Ticket subject"},
                        "description": {"type": "string", "description": "Ticket description"},
                        "priority": {"type": "string", "description": "Priority: low, normal, high, urgent", "default": "normal"},
                        "requester_email": {"type": "string", "description": "Requester email"},
                    },
                    "required": ["subject", "description"],
                },
            ),
            ToolFunction(
                name="get_tickets",
                description="Search or list tickets",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "status": {"type": "string", "description": "Filter: new, open, pending, solved"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="update_ticket",
                description="Update an existing ticket",
                parameters={
                    "type": "object",
                    "properties": {
                        "ticket_id": {"type": "string", "description": "Ticket ID"},
                        "status": {"type": "string", "description": "New status"},
                        "comment": {"type": "string", "description": "Add a comment"},
                        "priority": {"type": "string", "description": "New priority"},
                    },
                    "required": ["ticket_id"],
                },
            ),
        ],
        credential_env_vars=["TOOL_ZENDESK_SUBDOMAIN", "TOOL_ZENDESK_EMAIL", "TOOL_ZENDESK_API_TOKEN"],
        connector_file="zendesk.mjs",
        icon="zendesk",
    ),
    "teams": ToolDefinition(
        name="teams",
        description="Send messages to Microsoft Teams channels via webhook",
        category="communication",
        functions=[
            ToolFunction(
                name="send_message",
                description="Send a message to a Teams channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Message text (supports markdown)"},
                        "title": {"type": "string", "description": "Optional message card title"},
                    },
                    "required": ["text"],
                },
            ),
        ],
        credential_env_vars=["TOOL_TEAMS_WEBHOOK_URL"],
        connector_file="teams.mjs",
        icon="teams",
    ),
    "gdrive": ToolDefinition(
        name="gdrive",
        description="List, read, and create files in Google Drive",
        category="data",
        functions=[
            ToolFunction(
                name="list_files",
                description="List files in Google Drive",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query (Drive API format)"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="read_file",
                description="Read content of a Google Drive file",
                parameters={
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "File ID"},
                    },
                    "required": ["file_id"],
                },
            ),
            ToolFunction(
                name="create_file",
                description="Create a new file in Google Drive",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "File name"},
                        "content": {"type": "string", "description": "File content"},
                        "mime_type": {"type": "string", "description": "MIME type", "default": "text/plain"},
                        "parent_id": {"type": "string", "description": "Parent folder ID"},
                    },
                    "required": ["name", "content"],
                },
            ),
        ],
        credential_env_vars=["TOOL_GOOGLE_SERVICE_ACCOUNT"],
        connector_file="gdrive.mjs",
        icon="gdrive",
    ),
    "postgresql": ToolDefinition(
        name="postgresql",
        description="Execute SQL queries against a PostgreSQL database",
        category="data",
        functions=[
            ToolFunction(
                name="query",
                description="Execute a read-only SQL query",
                parameters={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL query to execute"},
                    },
                    "required": ["sql"],
                },
            ),
            ToolFunction(
                name="execute",
                description="Execute a write SQL statement (INSERT, UPDATE, DELETE)",
                parameters={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL statement to execute"},
                    },
                    "required": ["sql"],
                },
            ),
        ],
        credential_env_vars=["TOOL_POSTGRESQL_URL"],
        connector_file="postgresql.mjs",
        icon="database",
    ),
    "webhook": ToolDefinition(
        name="webhook",
        description="Make HTTP requests to external APIs (generic REST client)",
        category="general",
        functions=[
            ToolFunction(
                name="post",
                description="Send an HTTP POST request",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Target URL"},
                        "body": {"type": "object", "description": "Request body (JSON)"},
                        "headers": {"type": "object", "description": "Custom headers"},
                    },
                    "required": ["url"],
                },
            ),
            ToolFunction(
                name="get",
                description="Send an HTTP GET request",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Target URL"},
                        "headers": {"type": "object", "description": "Custom headers"},
                    },
                    "required": ["url"],
                },
            ),
        ],
        credential_env_vars=[],  # No default credentials - passed per call
        connector_file="webhook.mjs",
        icon="webhook",
    ),
}

# All known tool names (for YAML validation)
KNOWN_TOOLS = frozenset(TOOL_REGISTRY.keys())


def parse_tool_ref(ref: str) -> tuple[str, str | None]:
    """Parse a tool reference into (tool_name, connection_name).

    Examples:
        "slack"                -> ("slack", None)
        "postgresql:analytics" -> ("postgresql", "analytics")
    """
    if ":" in ref:
        tool_name, connection_name = ref.split(":", 1)
        return tool_name, connection_name
    return ref, None


def get_tool(name: str) -> ToolDefinition:
    """Get a tool definition by name. Raises KeyError if not found."""
    if name not in TOOL_REGISTRY:
        raise KeyError(
            f"Unknown tool '{name}'. "
            f"Available: {', '.join(sorted(TOOL_REGISTRY))}"
        )
    return TOOL_REGISTRY[name]


def list_tools(category: str | None = None) -> list[ToolDefinition]:
    """List all tools, optionally filtered by category."""
    tools = list(TOOL_REGISTRY.values())
    if category:
        tools = [t for t in tools if t.category == category]
    return sorted(tools, key=lambda t: t.name)


def validate_tools(tool_names: list[str]) -> list[str]:
    """Validate a list of tool names (supports tool:connection format).

    Returns list of error messages.
    """
    errors: list[str] = []
    for ref in tool_names:
        base_name, _ = parse_tool_ref(ref)
        if base_name not in TOOL_REGISTRY:
            errors.append(
                f"Unknown tool '{base_name}'. "
                f"Available: {', '.join(sorted(TOOL_REGISTRY))}"
            )
    return errors
