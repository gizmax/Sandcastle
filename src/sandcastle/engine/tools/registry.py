"""Tool Registry - definitions and metadata for all available connectors.

Each tool has a name, description, available functions, parameter schemas,
and required credential environment variable names. The registry is the
single source of truth used by the YAML validator, credential resolver,
loader, and dashboard UI.
"""

from __future__ import annotations

from dataclasses import dataclass


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
                        "channel": {
                            "type": "string",
                            "description": "Channel name or ID (e.g. '#general')",
                        },
                        "text": {
                            "type": "string",
                            "description": "Message text (supports Slack mrkdwn)",
                        },
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
                        "limit": {
                            "type": "integer",
                            "description": "Max channels to return",
                            "default": 100,
                        },
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
                        "limit": {
                            "type": "integer",
                            "description": "Max messages to return",
                            "default": 20,
                        },
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
                        "issue_type": {
                            "type": "string",
                            "description": "Issue type (Bug, Task, Story)",
                            "default": "Task",
                        },
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
                        "issue_key": {
                            "type": "string",
                            "description": "Issue key (e.g. 'PROJ-123')",
                        },
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
                        "max_results": {
                            "type": "integer",
                            "description": "Max results",
                            "default": 20,
                        },
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
                        "state": {
                            "type": "string",
                            "description": "Filter: open, closed, all",
                            "default": "open",
                        },
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
                        "base": {
                            "type": "string",
                            "description": "Target branch",
                            "default": "main",
                        },
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
                        "body": {
                            "type": "string",
                            "description": "Email body (plain text or HTML)",
                        },
                        "html": {
                            "type": "boolean",
                            "description": "Whether body is HTML",
                            "default": False,
                        },
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
        credential_env_vars=[
            "TOOL_SMTP_HOST",
            "TOOL_SMTP_PORT",
            "TOOL_SMTP_USER",
            "TOOL_SMTP_PASSWORD",
        ],
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
                        "filter_type": {
                            "type": "string",
                            "description": "Filter: page or database",
                        },
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
                        "parent_id": {
                            "type": "string",
                            "description": "Parent page or database ID",
                        },
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
                        "pipeline": {
                            "type": "string",
                            "description": "Pipeline name",
                            "default": "default",
                        },
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
                        "sobject": {
                            "type": "string",
                            "description": "SObject type (Account, Contact, Lead, etc.)",
                        },
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
        credential_env_vars=[
            "TOOL_SALESFORCE_CLIENT_ID",
            "TOOL_SALESFORCE_CLIENT_SECRET",
            "TOOL_SALESFORCE_REFRESH_TOKEN",
            "TOOL_SALESFORCE_INSTANCE_URL",
        ],
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
                        "priority": {
                            "type": "string",
                            "description": "Priority: low, normal, high, urgent",
                            "default": "normal",
                        },
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
                        "status": {
                            "type": "string",
                            "description": "Filter: new, open, pending, solved",
                        },
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
        credential_env_vars=[
            "TOOL_ZENDESK_SUBDOMAIN",
            "TOOL_ZENDESK_EMAIL",
            "TOOL_ZENDESK_API_TOKEN",
        ],
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
                        "text": {
                            "type": "string",
                            "description": "Message text (supports markdown)",
                        },
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
                        "query": {
                            "type": "string",
                            "description": "Search query (Drive API format)",
                        },
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
                        "mime_type": {
                            "type": "string",
                            "description": "MIME type",
                            "default": "text/plain",
                        },
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
    # --- Enterprise connectors ---
    "sap": ToolDefinition(
        name="sap",
        description=(
            "Search business partners, manage sales orders,"
            " and query materials in SAP S/4HANA"
        ),
        category="erp",
        functions=[
            ToolFunction(
                name="get_business_partners",
                description="Search business partners",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search by name"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_sales_orders",
                description="List sales orders with optional filters",
                parameters={
                    "type": "object",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "description": "Filter by customer (SoldToParty)",
                        },
                        "status": {"type": "string", "description": "Filter by process status"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_sales_order",
                description="Create a new sales order",
                parameters={
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer (SoldToParty)"},
                        "items": {
                            "type": "array",
                            "description": "Order items [{material, quantity}]",
                        },
                    },
                    "required": ["customer_id"],
                },
            ),
            ToolFunction(
                name="get_material",
                description="Get material/product details by ID",
                parameters={
                    "type": "object",
                    "properties": {
                        "material_id": {"type": "string", "description": "Material/product ID"},
                    },
                    "required": ["material_id"],
                },
            ),
        ],
        credential_env_vars=["TOOL_SAP_BASE_URL", "TOOL_SAP_API_KEY"],
        connector_file="sap.mjs",
        icon="sap",
    ),
    "servicenow": ToolDefinition(
        name="servicenow",
        description="Create, search, and manage incidents and change requests in ServiceNow",
        category="project_management",
        functions=[
            ToolFunction(
                name="get_incidents",
                description="Search incidents",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search in short_description"},
                        "state": {"type": "string", "description": "Filter by state"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_incident",
                description="Create a new incident",
                parameters={
                    "type": "object",
                    "properties": {
                        "short_description": {"type": "string", "description": "Incident title"},
                        "description": {"type": "string", "description": "Detailed description"},
                        "urgency": {
                            "type": "string",
                            "description": "Urgency: 1 (high), 2 (medium), 3 (low)",
                            "default": "2",
                        },
                        "category": {"type": "string", "description": "Incident category"},
                    },
                    "required": ["short_description"],
                },
            ),
            ToolFunction(
                name="update_incident",
                description="Update an existing incident",
                parameters={
                    "type": "object",
                    "properties": {
                        "sys_id": {"type": "string", "description": "Incident sys_id"},
                        "state": {"type": "string", "description": "New state"},
                        "work_notes": {"type": "string", "description": "Work notes to add"},
                    },
                    "required": ["sys_id"],
                },
            ),
            ToolFunction(
                name="get_change_requests",
                description="List change requests",
                parameters={
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "description": "Filter by state"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_SERVICENOW_INSTANCE",
            "TOOL_SERVICENOW_USERNAME",
            "TOOL_SERVICENOW_PASSWORD",
        ],
        connector_file="servicenow.mjs",
        icon="servicenow",
    ),
    "snowflake": ToolDefinition(
        name="snowflake",
        description="Execute SQL queries and explore schemas in Snowflake data warehouse",
        category="data",
        functions=[
            ToolFunction(
                name="execute_query",
                description="Run a SQL query",
                parameters={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL statement to execute"},
                        "database": {"type": "string", "description": "Target database"},
                        "schema": {"type": "string", "description": "Target schema"},
                        "limit": {
                            "type": "integer",
                            "description": "Max rows to return",
                            "default": 100,
                        },
                    },
                    "required": ["sql"],
                },
            ),
            ToolFunction(
                name="list_databases",
                description="List available databases",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            ToolFunction(
                name="list_tables",
                description="List tables in a schema",
                parameters={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "schema": {
                            "type": "string",
                            "description": "Schema name",
                            "default": "PUBLIC",
                        },
                    },
                    "required": ["database"],
                },
            ),
            ToolFunction(
                name="describe_table",
                description="Get table column definitions",
                parameters={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "schema": {
                            "type": "string",
                            "description": "Schema name",
                            "default": "PUBLIC",
                        },
                        "table": {"type": "string", "description": "Table name"},
                    },
                    "required": ["database", "table"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_SNOWFLAKE_ACCOUNT",
            "TOOL_SNOWFLAKE_USERNAME",
            "TOOL_SNOWFLAKE_PASSWORD",
            "TOOL_SNOWFLAKE_WAREHOUSE",
        ],
        connector_file="snowflake.mjs",
        icon="snowflake",
    ),
    "mongodb": ToolDefinition(
        name="mongodb",
        description="Query, insert, update, and aggregate documents in MongoDB via Atlas Data API",
        category="data",
        functions=[
            ToolFunction(
                name="find_documents",
                description="Query documents from a collection",
                parameters={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {"type": "object", "description": "MongoDB query filter"},
                        "limit": {
                            "type": "integer",
                            "description": "Max documents to return",
                            "default": 20,
                        },
                    },
                    "required": ["database", "collection"],
                },
            ),
            ToolFunction(
                name="insert_document",
                description="Insert a document into a collection",
                parameters={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "collection": {"type": "string", "description": "Collection name"},
                        "document": {"type": "object", "description": "Document to insert"},
                    },
                    "required": ["database", "collection", "document"],
                },
            ),
            ToolFunction(
                name="update_document",
                description="Update documents matching a filter",
                parameters={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {"type": "object", "description": "MongoDB query filter"},
                        "update": {
                            "type": "object",
                            "description": "Update operations (e.g. {$set: {field: value}})",
                        },
                    },
                    "required": ["database", "collection", "filter", "update"],
                },
            ),
            ToolFunction(
                name="aggregate",
                description="Run an aggregation pipeline",
                parameters={
                    "type": "object",
                    "properties": {
                        "database": {"type": "string", "description": "Database name"},
                        "collection": {"type": "string", "description": "Collection name"},
                        "pipeline": {"type": "array", "description": "Aggregation pipeline stages"},
                    },
                    "required": ["database", "collection", "pipeline"],
                },
            ),
        ],
        credential_env_vars=["TOOL_MONGODB_URI", "TOOL_MONGODB_API_KEY"],
        connector_file="mongodb.mjs",
        icon="mongodb",
    ),
    "stripe": ToolDefinition(
        name="stripe",
        description="Manage customers, invoices, and subscriptions in Stripe",
        category="payments",
        functions=[
            ToolFunction(
                name="list_customers",
                description="Search or list Stripe customers",
                parameters={
                    "type": "object",
                    "properties": {
                        "email": {"type": "string", "description": "Filter by email"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_customer",
                description="Get customer details with subscriptions",
                parameters={
                    "type": "object",
                    "properties": {
                        "customer_id": {
                            "type": "string",
                            "description": "Stripe customer ID (cus_...)",
                        },
                    },
                    "required": ["customer_id"],
                },
            ),
            ToolFunction(
                name="list_invoices",
                description="List invoices with optional filters",
                parameters={
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Filter by customer ID"},
                        "status": {
                            "type": "string",
                            "description": "Filter: draft, open, paid, void, uncollectible",
                        },
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_invoice",
                description="Create a draft invoice with line items",
                parameters={
                    "type": "object",
                    "properties": {
                        "customer_id": {"type": "string", "description": "Customer ID"},
                        "items": {
                            "type": "array",
                            "description": "Line items [{amount, currency, description}]",
                        },
                        "description": {"type": "string", "description": "Invoice description"},
                    },
                    "required": ["customer_id"],
                },
            ),
        ],
        credential_env_vars=["TOOL_STRIPE_SECRET_KEY"],
        connector_file="stripe.mjs",
        icon="stripe",
    ),
    "twilio": ToolDefinition(
        name="twilio",
        description="Send SMS, make calls, and manage messaging via Twilio",
        category="communication",
        functions=[
            ToolFunction(
                name="send_sms",
                description="Send an SMS message",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient phone number (+E.164 format)",
                        },
                        "body": {"type": "string", "description": "Message text"},
                    },
                    "required": ["to", "body"],
                },
            ),
            ToolFunction(
                name="list_messages",
                description="List message history",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Filter by recipient"},
                        "from": {"type": "string", "description": "Filter by sender"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_message",
                description="Get message details by SID",
                parameters={
                    "type": "object",
                    "properties": {
                        "message_sid": {"type": "string", "description": "Twilio message SID"},
                    },
                    "required": ["message_sid"],
                },
            ),
            ToolFunction(
                name="make_call",
                description="Initiate a voice call",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient phone number"},
                        "twiml_url": {
                            "type": "string",
                            "description": "URL returning TwiML instructions",
                        },
                    },
                    "required": ["to", "twiml_url"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_TWILIO_ACCOUNT_SID",
            "TOOL_TWILIO_AUTH_TOKEN",
            "TOOL_TWILIO_FROM_NUMBER",
        ],
        connector_file="twilio.mjs",
        icon="twilio",
    ),
    "sendgrid": ToolDefinition(
        name="sendgrid",
        description="Send transactional emails and manage marketing contacts via SendGrid",
        category="communication",
        functions=[
            ToolFunction(
                name="send_email",
                description="Send a transactional email",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Email subject"},
                        "html_content": {"type": "string", "description": "HTML email body"},
                        "text_content": {"type": "string", "description": "Plain text email body"},
                    },
                    "required": ["to", "subject"],
                },
            ),
            ToolFunction(
                name="list_contacts",
                description="Search marketing contacts",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search by email or name"},
                        "limit": {"type": "integer", "description": "Max results", "default": 50},
                    },
                },
            ),
            ToolFunction(
                name="add_contacts",
                description="Add or update marketing contacts",
                parameters={
                    "type": "object",
                    "properties": {
                        "contacts": {
                            "type": "array",
                            "description": "Contacts [{email, first_name, last_name}]",
                        },
                    },
                    "required": ["contacts"],
                },
            ),
            ToolFunction(
                name="get_stats",
                description="Get email delivery statistics",
                parameters={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                        "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                    },
                    "required": ["start_date"],
                },
            ),
        ],
        credential_env_vars=["TOOL_SENDGRID_API_KEY", "TOOL_SENDGRID_FROM_EMAIL"],
        connector_file="sendgrid.mjs",
        icon="sendgrid",
    ),
    "intercom": ToolDefinition(
        name="intercom",
        description="Search contacts, manage conversations, and send replies via Intercom",
        category="communication",
        functions=[
            ToolFunction(
                name="search_contacts",
                description="Search users and leads",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search by name or email"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_conversation",
                description="Start a new conversation with a contact",
                parameters={
                    "type": "object",
                    "properties": {
                        "contact_id": {"type": "string", "description": "Contact ID"},
                        "body": {"type": "string", "description": "Message body"},
                    },
                    "required": ["contact_id", "body"],
                },
            ),
            ToolFunction(
                name="reply_conversation",
                description="Reply to an existing conversation",
                parameters={
                    "type": "object",
                    "properties": {
                        "conversation_id": {"type": "string", "description": "Conversation ID"},
                        "body": {"type": "string", "description": "Reply body"},
                        "type": {
                            "type": "string",
                            "description": "Reply type: admin or user",
                            "default": "admin",
                        },
                    },
                    "required": ["conversation_id", "body"],
                },
            ),
            ToolFunction(
                name="list_conversations",
                description="List conversations",
                parameters={
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "description": "Filter: open, closed, snoozed"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
        ],
        credential_env_vars=["TOOL_INTERCOM_ACCESS_TOKEN"],
        connector_file="intercom.mjs",
        icon="intercom",
    ),
    "browser": ToolDefinition(
        name="browser",
        description=(
            "Playwright-based browser automation with CSS, text, and XPath selectors, "
            "plus AI-driven Computer Use and DOM extraction modes."
        ),
        category="general",
        functions=[
            ToolFunction(
                name="navigate",
                description="Navigate to a URL",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to navigate to"},
                    },
                    "required": ["url"],
                },
            ),
            ToolFunction(
                name="click",
                description="Click an element by selector",
                parameters={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector or text to match",
                        },
                        "method": {
                            "type": "string",
                            "description": (
                                "Selection method: 'css',"
                                " 'text', 'xpath'."
                                " Default: 'css'"
                            ),
                            "default": "css",
                        },
                    },
                    "required": ["selector"],
                },
            ),
            ToolFunction(
                name="type_text",
                description="Type text into an element",
                parameters={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector of the input field",
                        },
                        "text": {"type": "string", "description": "Text to type"},
                        "clear": {
                            "type": "boolean",
                            "description": "Clear field before typing. Default: true",
                            "default": True,
                        },
                    },
                    "required": ["selector", "text"],
                },
            ),
            ToolFunction(
                name="screenshot",
                description="Take a page screenshot",
                parameters={
                    "type": "object",
                    "properties": {
                        "full_page": {
                            "type": "boolean",
                            "description": "Capture full page. Default: false",
                            "default": False,
                        },
                    },
                },
            ),
            ToolFunction(
                name="get_text",
                description="Get text content of an element",
                parameters={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector to extract text from",
                        },
                    },
                    "required": ["selector"],
                },
            ),
            ToolFunction(
                name="wait_for",
                description="Wait for an element to appear",
                parameters={
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS selector to wait for"},
                        "timeout": {
                            "type": "number",
                            "description": "Max wait time in milliseconds. Default: 10000",
                            "default": 10000,
                        },
                    },
                    "required": ["selector"],
                },
            ),
            ToolFunction(
                name="select_option",
                description="Select a dropdown option",
                parameters={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": "CSS selector of the select element",
                        },
                        "value": {
                            "type": "string",
                            "description": "Option value or visible text to select",
                        },
                    },
                    "required": ["selector", "value"],
                },
            ),
            ToolFunction(
                name="get_page_html",
                description="Get the HTML of a section",
                parameters={
                    "type": "object",
                    "properties": {
                        "selector": {
                            "type": "string",
                            "description": (
                                "Optional CSS selector for"
                                " specific element."
                                " Default: 'body'"
                            ),
                            "default": "body",
                        },
                    },
                },
            ),
            ToolFunction(
                name="get_accessibility_tree",
                description="Get the accessibility tree for DOM extraction mode",
                parameters={
                    "type": "object",
                    "properties": {
                        "maxDepth": {
                            "type": "number",
                            "description": "Maximum depth to traverse. Default: 5",
                            "default": 5,
                        },
                    },
                },
            ),
            ToolFunction(
                name="get_interactive_elements",
                description="List all interactive elements with selectors and positions",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            ToolFunction(
                name="extract_structured",
                description="Extract page HTML for structured data extraction",
                parameters={
                    "type": "object",
                    "properties": {
                        "schema": {
                            "type": "object",
                            "description": "JSON schema describing desired output structure",
                        },
                    },
                },
            ),
            ToolFunction(
                name="get_page_info",
                description="Get page metadata including CAPTCHA detection",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="browser.mjs",
        icon="browser",
    ),
    # --- Tier 1: Core integrations ---
    "google-sheets": ToolDefinition(
        name="google-sheets",
        description="Read, write, and append data in Google Sheets spreadsheets",
        category="data",
        functions=[
            ToolFunction(
                name="read_range",
                description="Read cells from a range (e.g. Sheet1!A1:D10)",
                parameters={
                    "type": "object",
                    "properties": {
                        "range": {
                            "type": "string",
                            "description": "Range to read (e.g. Sheet1!A1:D10)",
                        },
                    },
                    "required": ["range"],
                },
            ),
            ToolFunction(
                name="write_range",
                description="Write a 2D array of values to a range",
                parameters={
                    "type": "object",
                    "properties": {
                        "range": {"type": "string"},
                        "values": {
                            "type": "array",
                            "description": "2D array of values",
                        },
                    },
                    "required": ["range", "values"],
                },
            ),
            ToolFunction(
                name="append_rows",
                description="Append rows to a sheet",
                parameters={
                    "type": "object",
                    "properties": {
                        "range": {"type": "string"},
                        "values": {"type": "array"},
                    },
                    "required": ["range", "values"],
                },
            ),
            ToolFunction(
                name="get_sheets",
                description="List all sheets in the spreadsheet",
                parameters={"type": "object", "properties": {}},
            ),
        ],
        credential_env_vars=[
            "TOOL_GOOGLE_SHEETS_SERVICE_ACCOUNT",
            "TOOL_GOOGLE_SHEETS_SPREADSHEET_ID",
        ],
        connector_file="google-sheets.mjs",
        icon="google-sheets",
    ),
    "airtable": ToolDefinition(
        name="airtable",
        description="CRUD operations on Airtable bases and tables",
        category="data",
        functions=[
            ToolFunction(
                name="list_records",
                description="List records with optional filter and sort",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["table"],
                },
            ),
            ToolFunction(
                name="get_record",
                description="Get a single record by ID",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "recordId": {"type": "string"},
                    },
                    "required": ["table", "recordId"],
                },
            ),
            ToolFunction(
                name="create_records",
                description="Create one or more records",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "records": {"type": "array"},
                    },
                    "required": ["table", "records"],
                },
            ),
            ToolFunction(
                name="update_records",
                description="Update existing records",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "records": {"type": "array"},
                    },
                    "required": ["table", "records"],
                },
            ),
            ToolFunction(
                name="delete_records",
                description="Delete records by IDs",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "recordIds": {"type": "array"},
                    },
                    "required": ["table", "recordIds"],
                },
            ),
        ],
        credential_env_vars=["TOOL_AIRTABLE_API_KEY", "TOOL_AIRTABLE_BASE_ID"],
        connector_file="airtable.mjs",
        icon="airtable",
    ),
    "linear": ToolDefinition(
        name="linear",
        description="Create, search, and manage issues in Linear",
        category="project_management",
        functions=[
            ToolFunction(
                name="create_issue",
                description="Create a new issue",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "teamId": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["title", "teamId"],
                },
            ),
            ToolFunction(
                name="get_issues",
                description="Search and filter issues",
                parameters={
                    "type": "object",
                    "properties": {"filter": {"type": "object"}},
                },
            ),
            ToolFunction(
                name="update_issue",
                description="Update issue fields",
                parameters={
                    "type": "object",
                    "properties": {
                        "issueId": {"type": "string"},
                        "updates": {"type": "object"},
                    },
                    "required": ["issueId", "updates"],
                },
            ),
            ToolFunction(
                name="get_teams",
                description="List all teams",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="add_comment",
                description="Add a comment to an issue",
                parameters={
                    "type": "object",
                    "properties": {
                        "issueId": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["issueId", "body"],
                },
            ),
        ],
        credential_env_vars=["TOOL_LINEAR_API_KEY"],
        connector_file="linear.mjs",
        icon="linear",
    ),
    "discord": ToolDefinition(
        name="discord",
        description="Send messages, create threads in Discord channels",
        category="communication",
        functions=[
            ToolFunction(
                name="send_message",
                description="Send a message to a channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "channelId": {"type": "string"},
                        "content": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["channelId", "content"],
                },
            ),
            ToolFunction(
                name="send_webhook",
                description="Send via webhook URL (no bot token needed)",
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["content"],
                },
            ),
            ToolFunction(
                name="get_messages",
                description="Read recent messages from a channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "channelId": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["channelId"],
                },
            ),
            ToolFunction(
                name="create_thread",
                description="Create a thread in a channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "channelId": {"type": "string"},
                        "name": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["channelId", "name"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_DISCORD_BOT_TOKEN",
            "TOOL_DISCORD_WEBHOOK_URL",
        ],
        connector_file="discord.mjs",
        icon="discord",
    ),
    "openai": ToolDefinition(
        name="openai",
        description=(
            "OpenAI API - chat completions, embeddings, image generation, "
            "speech-to-text, text-to-speech"
        ),
        category="ai",
        functions=[
            ToolFunction(
                name="chat_completion",
                description="Generate chat completion with GPT-4o/mini",
                parameters={
                    "type": "object",
                    "properties": {
                        "messages": {"type": "array"},
                        "model": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["messages"],
                },
            ),
            ToolFunction(
                name="create_embedding",
                description="Generate text embeddings",
                parameters={
                    "type": "object",
                    "properties": {
                        "input": {"type": "string"},
                        "model": {"type": "string"},
                    },
                    "required": ["input"],
                },
            ),
            ToolFunction(
                name="generate_image",
                description=(
                    "Generate image(s) with gpt-image-2 (default) or dall-e-3. "
                    "Writes files to disk and returns their paths. Pass "
                    "reference_images for faithful product/UGC editing."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "options": {
                            "type": "object",
                            "description": (
                                "model (gpt-image-2/dall-e-3), size or aspect "
                                "(1:1/4:5/9:16/16:9), quality (low/medium/high), n, "
                                "output, output_dir, reference_images (string[])"
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            ToolFunction(
                name="analyze_image",
                description=(
                    "Vision analysis/judging - send image(s) + a prompt to a vision "
                    "model (default gpt-5.2) and get the answer. Use to score generated "
                    "images against a reference. Returns JSON when the model replies JSON."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "options": {
                            "type": "object",
                            "description": (
                                "images (string[] of local paths or URLs), "
                                "model (default gpt-5.2), max_tokens"
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            ToolFunction(
                name="transcribe_audio",
                description="Transcribe audio with Whisper",
                parameters={
                    "type": "object",
                    "properties": {
                        "fileUrl": {"type": "string"},
                    },
                    "required": ["fileUrl"],
                },
            ),
            ToolFunction(
                name="text_to_speech",
                description="Convert text to speech audio",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "voice": {"type": "string"},
                    },
                    "required": ["text"],
                },
            ),
        ],
        credential_env_vars=["TOOL_OPENAI_API_KEY"],
        connector_file="openai.mjs",
        icon="openai",
    ),
    "anthropic": ToolDefinition(
        name="anthropic",
        description="Anthropic Claude API - chat completions and messages",
        category="ai",
        functions=[
            ToolFunction(
                name="chat_completion",
                description="Generate completion with Claude models",
                parameters={
                    "type": "object",
                    "properties": {
                        "messages": {"type": "array"},
                        "model": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["messages"],
                },
            ),
            ToolFunction(
                name="create_message",
                description="Simple single-turn message",
                parameters={
                    "type": "object",
                    "properties": {
                        "system": {"type": "string"},
                        "userMessage": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["userMessage"],
                },
            ),
        ],
        credential_env_vars=["TOOL_ANTHROPIC_API_KEY"],
        connector_file="anthropic.mjs",
        icon="anthropic",
    ),
    "aws-s3": ToolDefinition(
        name="aws-s3",
        description="AWS S3 object storage - list, get, put, delete objects",
        category="data",
        functions=[
            ToolFunction(
                name="list_objects",
                description="List objects in bucket with optional prefix",
                parameters={
                    "type": "object",
                    "properties": {
                        "prefix": {"type": "string"},
                        "maxKeys": {"type": "integer"},
                    },
                },
            ),
            ToolFunction(
                name="get_object",
                description="Download object content",
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            ),
            ToolFunction(
                name="put_object",
                description="Upload object to bucket",
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "body": {"type": "string"},
                        "contentType": {"type": "string"},
                    },
                    "required": ["key", "body"],
                },
            ),
            ToolFunction(
                name="delete_object",
                description="Delete object from bucket",
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            ),
            ToolFunction(
                name="generate_presigned_url",
                description="Generate presigned URL for sharing",
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "expiresIn": {"type": "integer"},
                    },
                    "required": ["key"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_AWS_ACCESS_KEY_ID",
            "TOOL_AWS_SECRET_ACCESS_KEY",
            "TOOL_AWS_REGION",
            "TOOL_AWS_S3_BUCKET",
        ],
        connector_file="aws-s3.mjs",
        icon="aws-s3",
    ),
    "redis": ToolDefinition(
        name="redis",
        description="Redis key-value store - get, set, delete, pub/sub",
        category="data",
        functions=[
            ToolFunction(
                name="get",
                description="Get value by key",
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            ),
            ToolFunction(
                name="set",
                description="Set key-value with optional TTL",
                parameters={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                        "ttl": {"type": "integer"},
                    },
                    "required": ["key", "value"],
                },
            ),
            ToolFunction(
                name="delete",
                description="Delete key",
                parameters={
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            ),
            ToolFunction(
                name="list_keys",
                description="List keys matching pattern",
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
            ),
            ToolFunction(
                name="publish",
                description="Publish message to channel",
                parameters={
                    "type": "object",
                    "properties": {
                        "channel": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["channel", "message"],
                },
            ),
        ],
        credential_env_vars=["TOOL_REDIS_URL", "TOOL_REDIS_TOKEN"],
        connector_file="redis.mjs",
        icon="redis",
    ),
    # --- Tier 2: Differentiators ---
    "supabase": ToolDefinition(
        name="supabase",
        description="Supabase - Postgres queries, RPC, and file storage",
        category="data",
        functions=[
            ToolFunction(
                name="query",
                description="Select rows with filters",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["table"],
                },
            ),
            ToolFunction(
                name="insert",
                description="Insert rows into table",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "records": {"type": "array"},
                    },
                    "required": ["table", "records"],
                },
            ),
            ToolFunction(
                name="update",
                description="Update matching rows",
                parameters={
                    "type": "object",
                    "properties": {
                        "table": {"type": "string"},
                        "match": {"type": "object"},
                        "data": {"type": "object"},
                    },
                    "required": ["table", "match", "data"],
                },
            ),
            ToolFunction(
                name="rpc",
                description="Call a Postgres function",
                parameters={
                    "type": "object",
                    "properties": {
                        "functionName": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["functionName"],
                },
            ),
        ],
        credential_env_vars=["TOOL_SUPABASE_URL", "TOOL_SUPABASE_SERVICE_KEY"],
        connector_file="supabase.mjs",
        icon="supabase",
    ),
    "pinecone": ToolDefinition(
        name="pinecone",
        description="Pinecone vector database - upsert, query, delete vectors",
        category="ai",
        functions=[
            ToolFunction(
                name="upsert",
                description="Upsert vectors with metadata",
                parameters={
                    "type": "object",
                    "properties": {"vectors": {"type": "array"}},
                    "required": ["vectors"],
                },
            ),
            ToolFunction(
                name="query",
                description="Similarity search",
                parameters={
                    "type": "object",
                    "properties": {
                        "vector": {"type": "array"},
                        "topK": {"type": "integer"},
                        "filter": {"type": "object"},
                    },
                    "required": ["vector", "topK"],
                },
            ),
            ToolFunction(
                name="delete_vectors",
                description="Delete vectors by IDs",
                parameters={
                    "type": "object",
                    "properties": {"ids": {"type": "array"}},
                    "required": ["ids"],
                },
            ),
            ToolFunction(
                name="describe_index",
                description="Get index statistics",
                parameters={"type": "object", "properties": {}},
            ),
        ],
        credential_env_vars=[
            "TOOL_PINECONE_API_KEY",
            "TOOL_PINECONE_INDEX_HOST",
        ],
        connector_file="pinecone.mjs",
        icon="pinecone",
    ),
    "resend": ToolDefinition(
        name="resend",
        description="Resend - modern developer email API",
        category="communication",
        functions=[
            ToolFunction(
                name="send_email",
                description="Send an email",
                parameters={
                    "type": "object",
                    "properties": {
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "html": {"type": "string"},
                    },
                    "required": ["from", "to", "subject", "html"],
                },
            ),
            ToolFunction(
                name="get_email",
                description="Get email delivery status",
                parameters={
                    "type": "object",
                    "properties": {"emailId": {"type": "string"}},
                    "required": ["emailId"],
                },
            ),
            ToolFunction(
                name="list_emails",
                description="List sent emails",
                parameters={"type": "object", "properties": {}},
            ),
        ],
        credential_env_vars=["TOOL_RESEND_API_KEY"],
        connector_file="resend.mjs",
        icon="resend",
    ),
    "vercel": ToolDefinition(
        name="vercel",
        description="Vercel - deployments, projects, and domains",
        category="devops",
        functions=[
            ToolFunction(
                name="list_deployments",
                description="List recent deployments",
                parameters={
                    "type": "object",
                    "properties": {"limit": {"type": "integer"}},
                },
            ),
            ToolFunction(
                name="get_deployment",
                description="Get deployment details and URL",
                parameters={
                    "type": "object",
                    "properties": {"deploymentId": {"type": "string"}},
                    "required": ["deploymentId"],
                },
            ),
            ToolFunction(
                name="list_projects",
                description="List all projects",
                parameters={"type": "object", "properties": {}},
            ),
        ],
        credential_env_vars=["TOOL_VERCEL_TOKEN"],
        connector_file="vercel.mjs",
        icon="vercel",
    ),
    "cloudflare-workers": ToolDefinition(
        name="cloudflare-workers",
        description="Cloudflare Workers - deploy edge functions and KV storage",
        category="devops",
        functions=[
            ToolFunction(
                name="list_workers",
                description="List all workers",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="deploy_worker",
                description="Deploy or update a worker script",
                parameters={
                    "type": "object",
                    "properties": {
                        "scriptName": {"type": "string"},
                        "script": {"type": "string"},
                    },
                    "required": ["scriptName", "script"],
                },
            ),
            ToolFunction(
                name="kv_get",
                description="Read KV value",
                parameters={
                    "type": "object",
                    "properties": {
                        "namespaceId": {"type": "string"},
                        "key": {"type": "string"},
                    },
                    "required": ["namespaceId", "key"],
                },
            ),
            ToolFunction(
                name="kv_put",
                description="Write KV value",
                parameters={
                    "type": "object",
                    "properties": {
                        "namespaceId": {"type": "string"},
                        "key": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["namespaceId", "key", "value"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_CLOUDFLARE_API_TOKEN",
            "TOOL_CLOUDFLARE_ACCOUNT_ID",
        ],
        connector_file="cloudflare-workers.mjs",
        icon="cloudflare",
    ),
    "firecrawl": ToolDefinition(
        name="firecrawl",
        description="Firecrawl - web scraping and crawling API",
        category="ai",
        functions=[
            ToolFunction(
                name="scrape",
                description="Scrape a URL to markdown/HTML",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["url"],
                },
            ),
            ToolFunction(
                name="crawl",
                description="Crawl a website",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["url"],
                },
            ),
            ToolFunction(
                name="map",
                description="Get site map of URLs",
                parameters={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            ),
        ],
        credential_env_vars=["TOOL_FIRECRAWL_API_KEY"],
        connector_file="firecrawl.mjs",
        icon="firecrawl",
    ),
    "websearch": ToolDefinition(
        name="websearch",
        description="Keyless free web search + clean content extraction (DuckDuckGo + Jina Reader; optional TOOL_JINA_API_KEY upgrades quality)",
        category="ai",
        functions=[
            ToolFunction(
                name="search",
                description="Keyless web search returning clean, LLM-ready content",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["query"],
                },
            ),
            ToolFunction(
                name="extract",
                description="Extract clean content from URLs",
                parameters={
                    "type": "object",
                    "properties": {"urls": {"type": "array"}},
                    "required": ["urls"],
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="websearch.mjs",
        icon="search",
    ),
    "tavily": ToolDefinition(
        name="tavily",
        description="Tavily - AI-optimized web search",
        category="ai",
        functions=[
            ToolFunction(
                name="search",
                description="AI-powered web search",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["query"],
                },
            ),
            ToolFunction(
                name="extract",
                description="Extract content from URLs",
                parameters={
                    "type": "object",
                    "properties": {"urls": {"type": "array"}},
                    "required": ["urls"],
                },
            ),
        ],
        credential_env_vars=["TOOL_TAVILY_API_KEY"],
        connector_file="tavily.mjs",
        icon="tavily",
    ),
    "elevenlabs": ToolDefinition(
        name="elevenlabs",
        description="ElevenLabs - text-to-speech and voice synthesis",
        category="ai",
        functions=[
            ToolFunction(
                name="text_to_speech",
                description="Generate speech audio from text",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "voiceId": {"type": "string"},
                    },
                    "required": ["text", "voiceId"],
                },
            ),
            ToolFunction(
                name="list_voices",
                description="List available voices",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="get_voice",
                description="Get voice details",
                parameters={
                    "type": "object",
                    "properties": {"voiceId": {"type": "string"}},
                    "required": ["voiceId"],
                },
            ),
        ],
        credential_env_vars=["TOOL_ELEVENLABS_API_KEY"],
        connector_file="elevenlabs.mjs",
        icon="elevenlabs",
    ),
    "nano-banana": ToolDefinition(
        name="nano-banana",
        description="AI image generation with Gemini - product photos, UGC shots, transparent assets",
        category="ai",
        functions=[
            ToolFunction(
                name="generate",
                description="Generate a single image from a text prompt",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Image generation prompt",
                        },
                        "options": {
                            "type": "object",
                            "description": "Generation options: size (512/1K/2K/4K), aspect (1:1/16:9/9:16/4:5), model (flash/pro), output, output_dir, reference_images (string[]), transparent (bool)",
                        },
                    },
                    "required": ["prompt"],
                },
            ),
            ToolFunction(
                name="batch_generate",
                description="Generate multiple images from an array of prompts",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prompt": {"type": "string"},
                                    "options": {"type": "object"},
                                },
                                "required": ["prompt"],
                            },
                            "description": "Array of {prompt, options?} objects",
                        },
                        "sharedOptions": {
                            "type": "object",
                            "description": "Shared options applied to all prompts",
                        },
                    },
                    "required": ["prompts"],
                },
            ),
            ToolFunction(
                name="costs",
                description="Show cost tracking summary for all generations",
                parameters={"type": "object", "properties": {}},
            ),
        ],
        credential_env_vars=["TOOL_NANO_BANANA_API_KEY"],
        connector_file="nano-banana.mjs",
        icon="image",
    ),
    "gemini": ToolDefinition(
        name="gemini",
        description="Gemini vision analysis - see and judge images (e.g. score UGC shots vs a reference)",
        category="ai",
        functions=[
            ToolFunction(
                name="analyze_image",
                description=(
                    "Send image(s) + a prompt to a Gemini vision model and get the "
                    "answer. Use to score generated images against a reference. "
                    "Returns JSON when the model replies JSON."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "options": {
                            "type": "object",
                            "description": (
                                "images (string[] of local paths or URLs), "
                                "model (default gemini-3.5-flash), max_output_tokens"
                            ),
                        },
                    },
                    "required": ["prompt"],
                },
            ),
        ],
        credential_env_vars=["TOOL_GEMINI_API_KEY"],
        connector_file="gemini.mjs",
        icon="image",
    ),
    # --- Tier 3: Bold integrations ---
    "zapier": ToolDefinition(
        name="zapier",
        description="Zapier webhook triggers - connect to 5000+ apps",
        category="general",
        functions=[
            ToolFunction(
                name="trigger",
                description="Send data to Zapier webhook",
                parameters={
                    "type": "object",
                    "properties": {"data": {"type": "object"}},
                    "required": ["data"],
                },
            ),
            ToolFunction(
                name="trigger_with_response",
                description="Send data and wait for Zapier response",
                parameters={
                    "type": "object",
                    "properties": {"data": {"type": "object"}},
                    "required": ["data"],
                },
            ),
        ],
        credential_env_vars=["TOOL_ZAPIER_WEBHOOK_URL"],
        connector_file="zapier.mjs",
        icon="zapier",
    ),
    "shopify": ToolDefinition(
        name="shopify",
        description="Shopify Admin API - products, orders, inventory",
        category="erp",
        functions=[
            ToolFunction(
                name="get_products",
                description="List products",
                parameters={
                    "type": "object",
                    "properties": {"options": {"type": "object"}},
                },
            ),
            ToolFunction(
                name="get_orders",
                description="List orders with filters",
                parameters={
                    "type": "object",
                    "properties": {"options": {"type": "object"}},
                },
            ),
            ToolFunction(
                name="create_product",
                description="Create a new product",
                parameters={
                    "type": "object",
                    "properties": {"data": {"type": "object"}},
                    "required": ["data"],
                },
            ),
            ToolFunction(
                name="update_inventory",
                description="Adjust inventory level",
                parameters={
                    "type": "object",
                    "properties": {
                        "inventoryItemId": {"type": "string"},
                        "quantity": {"type": "integer"},
                    },
                    "required": ["inventoryItemId", "quantity"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_SHOPIFY_STORE",
            "TOOL_SHOPIFY_ACCESS_TOKEN",
        ],
        connector_file="shopify.mjs",
        icon="shopify",
    ),
    "quickbooks": ToolDefinition(
        name="quickbooks",
        description="QuickBooks Online - invoices, payments, accounting queries",
        category="erp",
        functions=[
            ToolFunction(
                name="query",
                description="Run a QuickBooks query",
                parameters={
                    "type": "object",
                    "properties": {"sql": {"type": "string"}},
                    "required": ["sql"],
                },
            ),
            ToolFunction(
                name="create_invoice",
                description="Create an invoice",
                parameters={
                    "type": "object",
                    "properties": {
                        "customerRef": {"type": "string"},
                        "lineItems": {"type": "array"},
                    },
                    "required": ["customerRef", "lineItems"],
                },
            ),
            ToolFunction(
                name="create_payment",
                description="Record a payment",
                parameters={
                    "type": "object",
                    "properties": {
                        "customerRef": {"type": "string"},
                        "amount": {"type": "number"},
                        "invoiceRef": {"type": "string"},
                    },
                    "required": ["customerRef", "amount"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_QUICKBOOKS_CLIENT_ID",
            "TOOL_QUICKBOOKS_CLIENT_SECRET",
            "TOOL_QUICKBOOKS_REFRESH_TOKEN",
            "TOOL_QUICKBOOKS_REALM_ID",
        ],
        connector_file="quickbooks.mjs",
        icon="quickbooks",
    ),
    "helios": ToolDefinition(
        name="helios",
        description=(
            "Helios iNuvio (Asseco) - invoices, orders, contacts,"
            " and stock queries via REST API"
        ),
        category="erp",
        functions=[
            ToolFunction(
                name="get_contacts",
                description="Search contacts (business partners)",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search by name or ICO"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_invoices",
                description="List issued invoices with optional filters",
                parameters={
                    "type": "object",
                    "properties": {
                        "contact_id": {
                            "type": "string",
                            "description": "Filter by contact ID",
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by status (draft, issued, paid, overdue)",
                        },
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_invoice",
                description="Create a new issued invoice",
                parameters={
                    "type": "object",
                    "properties": {
                        "contact_id": {"type": "string", "description": "Contact (partner) ID"},
                        "items": {
                            "type": "array",
                            "description": "Invoice line items [{desc, qty, unit_price}]",
                        },
                        "due_days": {
                            "type": "integer",
                            "description": "Payment due in days",
                            "default": 14,
                        },
                    },
                    "required": ["contact_id", "items"],
                },
            ),
            ToolFunction(
                name="get_orders",
                description="List sales orders",
                parameters={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Filter by order status"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_stock",
                description="Query warehouse stock levels",
                parameters={
                    "type": "object",
                    "properties": {
                        "product_code": {
                            "type": "string",
                            "description": "Filter by product code/SKU",
                        },
                        "warehouse_id": {
                            "type": "string",
                            "description": "Filter by warehouse ID",
                        },
                    },
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_HELIOS_BASE_URL",
            "TOOL_HELIOS_API_KEY",
        ],
        connector_file="helios.mjs",
        icon="helios",
    ),
    "abra": ToolDefinition(
        name="abra",
        description=(
            "ABRA Gen (ABRA Software) - business partners, invoices,"
            " orders, and warehouse management via REST API"
        ),
        category="erp",
        functions=[
            ToolFunction(
                name="get_firms",
                description="Search business partners (firms)",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search by name or ICO"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_issued_invoices",
                description="List issued invoices with optional filters",
                parameters={
                    "type": "object",
                    "properties": {
                        "firm_id": {
                            "type": "string",
                            "description": "Filter by firm (partner) ID",
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by status (draft, posted, paid)",
                        },
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="create_issued_invoice",
                description="Create a new issued invoice",
                parameters={
                    "type": "object",
                    "properties": {
                        "firm_id": {"type": "string", "description": "Firm (partner) ID"},
                        "rows": {
                            "type": "array",
                            "description": "Invoice rows [{text, quantity, unit_price}]",
                        },
                        "due_days": {
                            "type": "integer",
                            "description": "Payment due in days",
                            "default": 14,
                        },
                    },
                    "required": ["firm_id", "rows"],
                },
            ),
            ToolFunction(
                name="get_orders",
                description="List sales orders",
                parameters={
                    "type": "object",
                    "properties": {
                        "firm_id": {"type": "string", "description": "Filter by firm ID"},
                        "limit": {"type": "integer", "description": "Max results", "default": 20},
                    },
                },
            ),
            ToolFunction(
                name="get_store_cards",
                description="Query warehouse store cards (stock items)",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search by name or code",
                        },
                        "store_id": {
                            "type": "string",
                            "description": "Filter by store (warehouse) ID",
                        },
                    },
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_ABRA_BASE_URL",
            "TOOL_ABRA_USERNAME",
            "TOOL_ABRA_PASSWORD",
        ],
        connector_file="abra.mjs",
        icon="abra",
    ),
    "calendly": ToolDefinition(
        name="calendly",
        description="Calendly - event types, scheduled events, cancellations",
        category="general",
        functions=[
            ToolFunction(
                name="list_event_types",
                description="List available event types",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="list_events",
                description="List scheduled events",
                parameters={
                    "type": "object",
                    "properties": {"options": {"type": "object"}},
                },
            ),
            ToolFunction(
                name="get_event",
                description="Get event details with invitees",
                parameters={
                    "type": "object",
                    "properties": {"eventUuid": {"type": "string"}},
                    "required": ["eventUuid"],
                },
            ),
            ToolFunction(
                name="cancel_event",
                description="Cancel a scheduled event",
                parameters={
                    "type": "object",
                    "properties": {
                        "eventUuid": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["eventUuid"],
                },
            ),
        ],
        credential_env_vars=["TOOL_CALENDLY_API_KEY"],
        connector_file="calendly.mjs",
        icon="calendly",
    ),
    "whatsapp": ToolDefinition(
        name="whatsapp",
        description="WhatsApp Business - messages, templates, media",
        category="communication",
        functions=[
            ToolFunction(
                name="send_message",
                description="Send a text message",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["to", "text"],
                },
            ),
            ToolFunction(
                name="send_template",
                description="Send a template message",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "templateName": {"type": "string"},
                        "parameters": {"type": "array"},
                    },
                    "required": ["to", "templateName"],
                },
            ),
            ToolFunction(
                name="send_media",
                description="Send image, video, or document",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "mediaUrl": {"type": "string"},
                        "type": {"type": "string"},
                    },
                    "required": ["to", "mediaUrl"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_WHATSAPP_TOKEN",
            "TOOL_WHATSAPP_PHONE_ID",
        ],
        connector_file="whatsapp.mjs",
        icon="whatsapp",
    ),
    "figma": ToolDefinition(
        name="figma",
        description="Figma - files, image exports, comments, components",
        category="general",
        functions=[
            ToolFunction(
                name="get_file",
                description="Get file metadata and page structure",
                parameters={
                    "type": "object",
                    "properties": {"fileKey": {"type": "string"}},
                    "required": ["fileKey"],
                },
            ),
            ToolFunction(
                name="get_images",
                description="Export nodes as PNG/SVG images",
                parameters={
                    "type": "object",
                    "properties": {
                        "fileKey": {"type": "string"},
                        "nodeIds": {"type": "string"},
                        "format": {"type": "string"},
                    },
                    "required": ["fileKey", "nodeIds"],
                },
            ),
            ToolFunction(
                name="post_comment",
                description="Add comment to a file",
                parameters={
                    "type": "object",
                    "properties": {
                        "fileKey": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["fileKey", "message"],
                },
            ),
        ],
        credential_env_vars=["TOOL_FIGMA_ACCESS_TOKEN"],
        connector_file="figma.mjs",
        icon="figma",
    ),
    "datadog": ToolDefinition(
        name="datadog",
        description="Datadog - metrics, logs, monitors, incidents",
        category="devops",
        functions=[
            ToolFunction(
                name="query_metrics",
                description="Query time-series metrics",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                    },
                    "required": ["query", "from", "to"],
                },
            ),
            ToolFunction(
                name="search_logs",
                description="Search logs",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "from": {"type": "string"},
                        "to": {"type": "string"},
                    },
                    "required": ["query", "from", "to"],
                },
            ),
            ToolFunction(
                name="list_monitors",
                description="List monitors with status",
                parameters={
                    "type": "object",
                    "properties": {"options": {"type": "object"}},
                },
            ),
            ToolFunction(
                name="create_incident",
                description="Create an incident",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "severity": {"type": "string"},
                    },
                    "required": ["title", "severity"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_DATADOG_API_KEY",
            "TOOL_DATADOG_APP_KEY",
        ],
        connector_file="datadog.mjs",
        icon="datadog",
    ),
    "plaid": ToolDefinition(
        name="plaid",
        description="Plaid - bank accounts, transactions, balances",
        category="payments",
        functions=[
            ToolFunction(
                name="get_accounts",
                description="List linked bank accounts",
                parameters={
                    "type": "object",
                    "properties": {"accessToken": {"type": "string"}},
                    "required": ["accessToken"],
                },
            ),
            ToolFunction(
                name="get_transactions",
                description="Get account transactions",
                parameters={
                    "type": "object",
                    "properties": {
                        "accessToken": {"type": "string"},
                        "startDate": {"type": "string"},
                        "endDate": {"type": "string"},
                    },
                    "required": ["accessToken", "startDate", "endDate"],
                },
            ),
            ToolFunction(
                name="get_balance",
                description="Get real-time account balances",
                parameters={
                    "type": "object",
                    "properties": {"accessToken": {"type": "string"}},
                    "required": ["accessToken"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_PLAID_CLIENT_ID",
            "TOOL_PLAID_SECRET",
        ],
        connector_file="plaid.mjs",
        icon="plaid",
    ),
    "docusign": ToolDefinition(
        name="docusign",
        description="DocuSign - create, track, and download signed documents",
        category="general",
        functions=[
            ToolFunction(
                name="create_envelope",
                description="Send document for signature",
                parameters={
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "recipients": {"type": "array"},
                        "documentBase64": {"type": "string"},
                    },
                    "required": ["subject", "recipients", "documentBase64"],
                },
            ),
            ToolFunction(
                name="get_envelope",
                description="Get envelope/signature status",
                parameters={
                    "type": "object",
                    "properties": {"envelopeId": {"type": "string"}},
                    "required": ["envelopeId"],
                },
            ),
            ToolFunction(
                name="list_envelopes",
                description="List envelopes with filters",
                parameters={
                    "type": "object",
                    "properties": {"options": {"type": "object"}},
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_DOCUSIGN_ACCESS_TOKEN",
            "TOOL_DOCUSIGN_ACCOUNT_ID",
        ],
        connector_file="docusign.mjs",
        icon="docusign",
    ),
    "pagerduty": ToolDefinition(
        name="pagerduty",
        description="PagerDuty - incidents, acknowledgments, resolution",
        category="devops",
        functions=[
            ToolFunction(
                name="create_incident",
                description="Create/trigger an incident",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "serviceId": {"type": "string"},
                        "urgency": {"type": "string"},
                    },
                    "required": ["title", "serviceId"],
                },
            ),
            ToolFunction(
                name="list_incidents",
                description="List incidents",
                parameters={
                    "type": "object",
                    "properties": {"options": {"type": "object"}},
                },
            ),
            ToolFunction(
                name="acknowledge_incident",
                description="Acknowledge an incident",
                parameters={
                    "type": "object",
                    "properties": {"incidentId": {"type": "string"}},
                    "required": ["incidentId"],
                },
            ),
            ToolFunction(
                name="resolve_incident",
                description="Resolve an incident",
                parameters={
                    "type": "object",
                    "properties": {"incidentId": {"type": "string"}},
                    "required": ["incidentId"],
                },
            ),
        ],
        credential_env_vars=["TOOL_PAGERDUTY_API_KEY"],
        connector_file="pagerduty.mjs",
        icon="pagerduty",
    ),
    # --- Tier 4: AI-native tools ---
    "mcp-bridge": ToolDefinition(
        name="mcp-bridge",
        description="MCP Bridge - connect any MCP server as a tool",
        category="ai",
        functions=[
            ToolFunction(
                name="list_tools",
                description="Discover tools from MCP server",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="call_tool",
                description="Call a tool on the MCP server",
                parameters={
                    "type": "object",
                    "properties": {
                        "toolName": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["toolName"],
                },
            ),
            ToolFunction(
                name="list_resources",
                description="List MCP resources",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="read_resource",
                description="Read an MCP resource",
                parameters={
                    "type": "object",
                    "properties": {"uri": {"type": "string"}},
                    "required": ["uri"],
                },
            ),
        ],
        credential_env_vars=["TOOL_MCP_SERVER_URL"],
        connector_file="mcp-bridge.mjs",
        icon="mcp",
    ),
    "human-input": ToolDefinition(
        name="human-input",
        description=(
            "Human-in-the-Loop - collect structured input, approvals, "
            "and content reviews from humans"
        ),
        category="general",
        functions=[
            ToolFunction(
                name="request_input",
                description="Request structured data from a human",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "schema": {"type": "object"},
                    },
                    "required": ["prompt", "schema"],
                },
            ),
            ToolFunction(
                name="check_input",
                description="Check if human has responded",
                parameters={
                    "type": "object",
                    "properties": {"requestId": {"type": "string"}},
                    "required": ["requestId"],
                },
            ),
            ToolFunction(
                name="request_approval",
                description="Request yes/no approval",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "details": {"type": "string"},
                    },
                    "required": ["title"],
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="human-input.mjs",
        icon="human",
    ),
    "filesystem": ToolDefinition(
        name="filesystem",
        description="File system operations in sandbox - read, write, list, search",
        category="general",
        functions=[
            ToolFunction(
                name="read_file",
                description="Read file content",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            ToolFunction(
                name="write_file",
                description="Write content to file",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolFunction(
                name="list_directory",
                description="List directory contents",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["path"],
                },
            ),
            ToolFunction(
                name="search_files",
                description="Search files by glob pattern",
                parameters={
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string"},
                        "pattern": {"type": "string"},
                    },
                    "required": ["directory", "pattern"],
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="filesystem.mjs",
        icon="filesystem",
    ),
    "shell": ToolDefinition(
        name="shell",
        description="Execute shell commands and scripts in sandbox",
        category="general",
        functions=[
            ToolFunction(
                name="execute",
                description="Run a shell command",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["command"],
                },
            ),
            ToolFunction(
                name="execute_script",
                description="Run a multi-line script",
                parameters={
                    "type": "object",
                    "properties": {
                        "script": {"type": "string"},
                        "interpreter": {"type": "string"},
                    },
                    "required": ["script"],
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="shell.mjs",
        icon="shell",
    ),
    "python-runtime": ToolDefinition(
        name="python-runtime",
        description="Execute Python code snippets with data processing",
        category="general",
        functions=[
            ToolFunction(
                name="execute",
                description="Run Python code",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["code"],
                },
            ),
            ToolFunction(
                name="execute_with_data",
                description="Run code with JSON data input",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "inputData": {"type": "object"},
                    },
                    "required": ["code", "inputData"],
                },
            ),
            ToolFunction(
                name="install_packages",
                description="Install Python packages",
                parameters={
                    "type": "object",
                    "properties": {"packages": {"type": "array"}},
                    "required": ["packages"],
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="python-runtime.mjs",
        icon="python",
    ),
    "code-interpreter": ToolDefinition(
        name="code-interpreter",
        description=(
            "Jupyter-style code execution with data analysis and charting"
        ),
        category="ai",
        functions=[
            ToolFunction(
                name="run_cell",
                description="Execute a code cell",
                parameters={
                    "type": "object",
                    "properties": {
                        "code": {"type": "string"},
                        "language": {"type": "string"},
                    },
                    "required": ["code"],
                },
            ),
            ToolFunction(
                name="run_analysis",
                description="Auto-analyze data with pandas",
                parameters={
                    "type": "object",
                    "properties": {
                        "data": {"type": "object"},
                        "question": {"type": "string"},
                    },
                    "required": ["data", "question"],
                },
            ),
            ToolFunction(
                name="create_chart",
                description="Generate a chart as base64 PNG",
                parameters={
                    "type": "object",
                    "properties": {
                        "data": {"type": "object"},
                        "chartType": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["data", "chartType"],
                },
            ),
        ],
        credential_env_vars=[],
        connector_file="code-interpreter.mjs",
        icon="code",
    ),
    "composio": ToolDefinition(
        name="composio",
        description="Composio - unified API for 500+ business app integrations (Gmail, Slack, GitHub, Salesforce, etc.)",
        category="ai",
        functions=[
            ToolFunction(
                name="execute_action",
                description="Execute a Composio action (e.g. gmail_send_email, slack_send_message)",
                parameters={
                    "type": "object",
                    "properties": {
                        "actionName": {
                            "type": "string",
                            "description": "Composio action ID (e.g. 'gmail_send_email')",
                        },
                        "params": {
                            "type": "object",
                            "description": "Action-specific parameters",
                        },
                        "connectedAccountId": {
                            "type": "string",
                            "description": "Composio connected account ID (optional)",
                        },
                    },
                    "required": ["actionName"],
                },
            ),
            ToolFunction(
                name="list_actions",
                description="List available Composio actions, optionally filtered by app",
                parameters={
                    "type": "object",
                    "properties": {
                        "appName": {
                            "type": "string",
                            "description": "Filter by app name (e.g. 'github', 'gmail')",
                        },
                        "limit": {"type": "integer", "description": "Max results (default 20)"},
                    },
                },
            ),
            ToolFunction(
                name="list_apps",
                description="List all available Composio apps",
                parameters={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Max results (default 50)"},
                    },
                },
            ),
            ToolFunction(
                name="get_action_schema",
                description="Get the input/output schema for a specific Composio action",
                parameters={
                    "type": "object",
                    "properties": {
                        "actionName": {
                            "type": "string",
                            "description": "Composio action ID",
                        },
                    },
                    "required": ["actionName"],
                },
            ),
        ],
        credential_env_vars=["TOOL_COMPOSIO_API_KEY"],
        connector_file="composio.mjs",
        icon="composio",
    ),
    # --- v0.23: Ecosystem integrations ---
    "langfuse": ToolDefinition(
        name="langfuse",
        description="Langfuse - LLM observability: traces, generations, and scores",
        category="observability",
        functions=[
            ToolFunction(
                name="create_trace",
                description="Create a new trace for an LLM request",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Trace name"},
                        "metadata": {
                            "type": "object",
                            "description": "Optional metadata key-value pairs",
                        },
                    },
                    "required": ["name"],
                },
            ),
            ToolFunction(
                name="create_generation",
                description="Record an LLM generation inside a trace",
                parameters={
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string", "description": "Parent trace ID"},
                        "name": {"type": "string", "description": "Generation name"},
                        "model": {"type": "string", "description": "Model name (e.g. gpt-5.2)"},
                        "input": {"type": "object", "description": "Input messages/prompt"},
                        "output": {"type": "object", "description": "Model output"},
                        "usage": {
                            "type": "object",
                            "description": "Token usage {input, output, total}",
                        },
                    },
                    "required": ["trace_id", "name", "model", "input", "output"],
                },
            ),
            ToolFunction(
                name="create_score",
                description="Attach a numeric score to a trace",
                parameters={
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string", "description": "Trace ID to score"},
                        "name": {"type": "string", "description": "Score name (e.g. 'quality')"},
                        "value": {
                            "type": "number",
                            "description": "Numeric score value",
                        },
                        "comment": {
                            "type": "string",
                            "description": "Optional comment",
                        },
                    },
                    "required": ["trace_id", "name", "value"],
                },
            ),
            ToolFunction(
                name="get_trace",
                description="Retrieve a trace with all observations and scores",
                parameters={
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string", "description": "Trace ID to retrieve"},
                    },
                    "required": ["trace_id"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_LANGFUSE_SECRET_KEY",
            "TOOL_LANGFUSE_PUBLIC_KEY",
        ],
        connector_file="langfuse.mjs",
        icon="langfuse",
    ),
    "qdrant": ToolDefinition(
        name="qdrant",
        description="Qdrant vector database - search, upsert, and manage vector collections",
        category="data",
        functions=[
            ToolFunction(
                name="search",
                description="Similarity search in a collection",
                parameters={
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "vector": {
                            "type": "array",
                            "description": "Query vector (float array)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results to return",
                            "default": 10,
                        },
                        "filter": {
                            "type": "object",
                            "description": "Qdrant filter conditions",
                        },
                    },
                    "required": ["collection", "vector"],
                },
            ),
            ToolFunction(
                name="upsert",
                description="Insert or update points in a collection",
                parameters={
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "points": {
                            "type": "array",
                            "description": "Points to upsert [{id, vector, payload}]",
                        },
                    },
                    "required": ["collection", "points"],
                },
            ),
            ToolFunction(
                name="get_collections",
                description="List all collections in the Qdrant instance",
                parameters={"type": "object", "properties": {}},
            ),
            ToolFunction(
                name="delete_points",
                description="Delete points matching a filter from a collection",
                parameters={
                    "type": "object",
                    "properties": {
                        "collection": {"type": "string", "description": "Collection name"},
                        "filter": {
                            "type": "object",
                            "description": "Qdrant filter conditions for points to delete",
                        },
                    },
                    "required": ["collection", "filter"],
                },
            ),
        ],
        credential_env_vars=["TOOL_QDRANT_URL"],
        connector_file="qdrant.mjs",
        icon="qdrant",
    ),
    "gcs": ToolDefinition(
        name="gcs",
        description="Google Cloud Storage - upload, download, list, and delete objects",
        category="storage",
        functions=[
            ToolFunction(
                name="upload",
                description="Upload an object to a GCS bucket",
                parameters={
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "GCS bucket name"},
                        "path": {"type": "string", "description": "Object path/name in bucket"},
                        "content": {"type": "string", "description": "Object content"},
                        "content_type": {
                            "type": "string",
                            "description": "MIME type",
                            "default": "application/octet-stream",
                        },
                    },
                    "required": ["bucket", "path", "content"],
                },
            ),
            ToolFunction(
                name="download",
                description="Download an object from a GCS bucket",
                parameters={
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "GCS bucket name"},
                        "path": {"type": "string", "description": "Object path/name in bucket"},
                    },
                    "required": ["bucket", "path"],
                },
            ),
            ToolFunction(
                name="list_objects",
                description="List objects in a GCS bucket with optional prefix filter",
                parameters={
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "GCS bucket name"},
                        "prefix": {
                            "type": "string",
                            "description": "Filter objects by prefix",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max objects to return",
                            "default": 100,
                        },
                    },
                    "required": ["bucket"],
                },
            ),
            ToolFunction(
                name="delete_object",
                description="Delete an object from a GCS bucket",
                parameters={
                    "type": "object",
                    "properties": {
                        "bucket": {"type": "string", "description": "GCS bucket name"},
                        "path": {"type": "string", "description": "Object path/name to delete"},
                    },
                    "required": ["bucket", "path"],
                },
            ),
        ],
        credential_env_vars=[
            "TOOL_GCS_SERVICE_ACCOUNT_JSON",
            "TOOL_GCS_PROJECT_ID",
        ],
        connector_file="gcs.mjs",
        icon="gcs",
    ),
    "azure-blob": ToolDefinition(
        name="azure-blob",
        description="Azure Blob Storage - upload, download, list, and delete blobs",
        category="storage",
        functions=[
            ToolFunction(
                name="upload",
                description="Upload a blob to an Azure container",
                parameters={
                    "type": "object",
                    "properties": {
                        "container": {"type": "string", "description": "Container name"},
                        "blob_name": {"type": "string", "description": "Blob name/path"},
                        "content": {"type": "string", "description": "Blob content"},
                        "content_type": {
                            "type": "string",
                            "description": "MIME type",
                            "default": "application/octet-stream",
                        },
                    },
                    "required": ["container", "blob_name", "content"],
                },
            ),
            ToolFunction(
                name="download",
                description="Download a blob from an Azure container",
                parameters={
                    "type": "object",
                    "properties": {
                        "container": {"type": "string", "description": "Container name"},
                        "blob_name": {"type": "string", "description": "Blob name/path"},
                    },
                    "required": ["container", "blob_name"],
                },
            ),
            ToolFunction(
                name="list_blobs",
                description="List blobs in an Azure container with optional prefix filter",
                parameters={
                    "type": "object",
                    "properties": {
                        "container": {"type": "string", "description": "Container name"},
                        "prefix": {
                            "type": "string",
                            "description": "Filter blobs by prefix",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Max blobs to return",
                            "default": 100,
                        },
                    },
                    "required": ["container"],
                },
            ),
            ToolFunction(
                name="delete_blob",
                description="Delete a blob from an Azure container",
                parameters={
                    "type": "object",
                    "properties": {
                        "container": {"type": "string", "description": "Container name"},
                        "blob_name": {"type": "string", "description": "Blob name/path to delete"},
                    },
                    "required": ["container", "blob_name"],
                },
            ),
        ],
        credential_env_vars=["TOOL_AZURE_STORAGE_CONNECTION_STRING"],
        connector_file="azure-blob.mjs",
        icon="azure",
    ),
    "exa": ToolDefinition(
        name="exa",
        description="Exa - neural web search, content retrieval, and similar-page discovery",
        category="ai",
        functions=[
            ToolFunction(
                name="search",
                description="Neural web search returning ranked results",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "num_results": {
                            "type": "integer",
                            "description": "Number of results to return",
                            "default": 10,
                        },
                        "type": {
                            "type": "string",
                            "description": "Search type: auto, neural, keyword",
                            "default": "auto",
                        },
                        "use_autoprompt": {
                            "type": "boolean",
                            "description": "Let Exa rewrite query for better results",
                            "default": False,
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolFunction(
                name="get_contents",
                description="Retrieve full text content for a list of result IDs",
                parameters={
                    "type": "object",
                    "properties": {
                        "ids": {
                            "type": "array",
                            "description": "List of Exa result IDs",
                        },
                        "text": {
                            "type": "boolean",
                            "description": "Include full text content",
                            "default": True,
                        },
                        "highlights": {
                            "type": "boolean",
                            "description": "Include highlighted excerpts",
                            "default": False,
                        },
                    },
                    "required": ["ids"],
                },
            ),
            ToolFunction(
                name="find_similar",
                description="Find pages similar to a given URL",
                parameters={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Source URL to find similar pages for"},
                        "num_results": {
                            "type": "integer",
                            "description": "Number of similar results to return",
                            "default": 10,
                        },
                    },
                    "required": ["url"],
                },
            ),
        ],
        credential_env_vars=["TOOL_EXA_API_KEY"],
        connector_file="exa.mjs",
        icon="exa",
    ),
}

# All known tool names (for YAML validation)
KNOWN_TOOLS = frozenset(TOOL_REGISTRY.keys())

# Valid tool categories (for API validation)
VALID_CATEGORIES = frozenset(
    {t.category for t in TOOL_REGISTRY.values()}
)

# Connection name validation: alphanumeric, hyphens, underscores only
import re as _re  # noqa: E402

_CONN_NAME_RE = _re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")


def parse_tool_ref(ref: str) -> tuple[str, str | None]:
    """Parse a tool reference into (tool_name, connection_name).

    Connection names are validated to contain only safe characters
    (alphanumeric, hyphens, underscores) and max 100 chars.

    Examples:
        "slack"                -> ("slack", None)
        "postgresql:analytics" -> ("postgresql", "analytics")

    Raises ValueError if connection name contains invalid characters.
    """
    if ":" in ref:
        tool_name, connection_name = ref.split(":", 1)
        if not connection_name:
            raise ValueError(
                f"Empty connection name in tool ref '{ref}'"
            )
        if not _CONN_NAME_RE.match(connection_name):
            raise ValueError(
                f"Invalid connection name '{connection_name}' in tool ref '{ref}'. "
                "Must be 1-100 chars, alphanumeric/hyphens/underscores, "
                "starting with alphanumeric."
            )
        return tool_name, connection_name
    return ref, None


def get_tool(name: str) -> ToolDefinition:
    """Get a tool definition by name. Raises KeyError if not found."""
    if name not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool '{name}'. Available: {', '.join(sorted(TOOL_REGISTRY))}")
    return TOOL_REGISTRY[name]


def list_tools(category: str | None = None) -> list[ToolDefinition]:
    """List all tools, optionally filtered by category.

    If category is provided but not recognized, returns an empty list.
    """
    tools = list(TOOL_REGISTRY.values())
    if category:
        tools = [t for t in tools if t.category == category]
    return sorted(tools, key=lambda t: t.name)


def validate_tools(tool_names: list[str]) -> list[str]:
    """Validate a list of tool names (supports tool:connection format).

    Returns list of error messages. Validates both tool name existence
    and connection name format.
    """
    errors: list[str] = []
    for ref in tool_names:
        try:
            base_name, _ = parse_tool_ref(ref)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if base_name not in TOOL_REGISTRY:
            errors.append(
                f"Unknown tool '{base_name}'. Available: {', '.join(sorted(TOOL_REGISTRY))}"
            )
    return errors
