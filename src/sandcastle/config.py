"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings

_DEFAULT_DATA_DIR = str(Path.home() / ".sandcastle" / "data")
_DEFAULT_WORKFLOWS_DIR = str(Path.home() / ".sandcastle" / "workflows")


class Settings(BaseSettings):
    """Sandcastle configuration."""

    # Runtime connection
    anthropic_api_key: str = ""
    e2b_api_key: str = ""

    # Multi-model provider keys (optional)
    minimax_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""

    # E2B custom template (pre-built sandbox with SDK installed)
    e2b_template: str = ""  # e.g. "sandcastle-runner"

    # Sandbox backend: "e2b" | "docker" | "local" | "cloudflare"
    sandbox_backend: str = "e2b"

    # Docker backend settings
    docker_image: str = "sandcastle-runner:latest"
    docker_url: str = ""  # empty = local Docker socket

    # Cloudflare backend settings
    cloudflare_worker_url: str = ""  # e.g. "https://sandbox.your-domain.workers.dev"

    # Max concurrent sandboxes (prevents rate limiting)
    max_concurrent_sandboxes: int = 5

    # Database (empty = local SQLite mode)
    database_url: str = ""

    # Redis (empty = in-process queue)
    redis_url: str = ""

    # Storage
    storage_backend: str = "local"  # "s3" or "local"
    storage_bucket: str = "sandcastle-data"
    storage_endpoint: str = "http://localhost:9000"
    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"

    # Local mode data directory (default: ~/.sandcastle/data)
    data_dir: str = _DEFAULT_DATA_DIR

    # Sandbox root for filesystem operations (browse, csv_output).
    # Empty = no restriction (current behavior). Set to e.g. "./data" to restrict.
    sandbox_root: str = ""

    # Webhooks
    webhook_secret: str = ""

    # Auth
    auth_required: bool = False  # Set to True to enforce API key auth
    dashboard_origin: str = "http://localhost:5173"

    # Budget
    default_max_cost_usd: float = 0.0  # 0 = no limit

    # Workflows directory (default: ~/.sandcastle/workflows)
    workflows_dir: str = _DEFAULT_WORKFLOWS_DIR

    # Hierarchical workflows
    max_workflow_depth: int = 5

    # Scheduler (disable in multi-worker deployments; run a dedicated scheduler service)
    scheduler_enabled: bool = True

    # Admin bootstrap key (auto-created on startup if set and not yet in DB)
    admin_api_key: str = ""

    # Model failover
    failover_cooldown_seconds: float = 60.0

    # Tool connector credentials
    tool_slack_bot_token: str = ""
    tool_jira_api_token: str = ""
    tool_jira_base_url: str = ""
    tool_jira_email: str = ""
    tool_github_token: str = ""
    tool_notion_api_key: str = ""
    tool_hubspot_api_key: str = ""
    tool_salesforce_client_id: str = ""
    tool_salesforce_client_secret: str = ""
    tool_salesforce_refresh_token: str = ""
    tool_salesforce_instance_url: str = ""
    tool_zendesk_subdomain: str = ""
    tool_zendesk_email: str = ""
    tool_zendesk_api_token: str = ""
    tool_teams_webhook_url: str = ""
    tool_google_service_account: str = ""
    tool_postgresql_url: str = ""
    tool_smtp_host: str = ""
    tool_smtp_port: str = "587"
    tool_smtp_user: str = ""
    tool_smtp_password: str = ""

    # Memory
    memory_enabled: bool = True
    memory_backend: str = "local"  # "local" | "cloud"
    memory_graph_enabled: bool = False
    memory_max_age_days: int = 90  # TTL for memory decay (0 = no expiry)
    memory_admit_threshold: float = 0.3  # Minimum importance score to store

    # Security
    credential_encryption_key: str = ""  # Fernet key for encrypting tool credentials at rest
    key_rotation_grace_hours: int = 24  # Grace period for old key after rotation
    csp_report_only: bool = False  # Set to True for Content-Security-Policy-Report-Only

    # Docker hardening
    docker_seccomp_profile: str = ""  # Path to seccomp JSON profile (empty = bundled default)
    docker_pids_limit: int = 100  # Max PIDs per container
    docker_cpu_period: int = 100_000  # CPU period in microseconds
    docker_cpu_quota: int = 50_000  # CPU quota in microseconds (50% of one core)

    # License
    license_key: str = ""  # Ed25519-signed license key (sc_lic_...)

    # Telemetry (opt-in error reporting)
    telemetry_enabled: bool = False  # Set to True to send error reports via Sentry
    sentry_dsn: str = ""  # Sentry DSN - get one free at sentry.io

    # Logging
    log_level: str = "info"

    @field_validator("data_dir", "workflows_dir", mode="after")
    @classmethod
    def _expand_home(cls, v: str) -> str:
        """Expand ~ to the user's home directory."""
        return str(Path(v).expanduser())

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @computed_field
    @property
    def is_local_mode(self) -> bool:
        """True when running in local mode (SQLite + filesystem + in-process queue)."""
        return not self.database_url or self.database_url.startswith("sqlite")


settings = Settings()
