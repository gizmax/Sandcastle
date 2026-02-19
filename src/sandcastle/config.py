"""Application configuration loaded from environment variables."""

from pydantic import computed_field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Sandcastle configuration."""

    # Runtime connection
    sandstorm_url: str = ""  # Deprecated: legacy proxy URL (optional fallback)
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

    # Local mode data directory
    data_dir: str = "./data"

    # Sandbox root for filesystem operations (browse, csv_output).
    # Empty = no restriction (current behavior). Set to e.g. "./data" to restrict.
    sandbox_root: str = ""

    # Webhooks
    webhook_secret: str = "your-webhook-signing-secret"

    # Auth
    auth_required: bool = False  # Set to True to enforce API key auth
    dashboard_origin: str = "http://localhost:5173"

    # Budget
    default_max_cost_usd: float = 0.0  # 0 = no limit

    # Workflows directory
    workflows_dir: str = "./workflows"

    # Hierarchical workflows
    max_workflow_depth: int = 5

    # Scheduler (disable in multi-worker deployments; run a dedicated scheduler service)
    scheduler_enabled: bool = True

    # Admin bootstrap key (auto-created on startup if set and not yet in DB)
    admin_api_key: str = ""

    # Model failover
    failover_cooldown_seconds: float = 60.0

    # Logging
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @computed_field
    @property
    def is_local_mode(self) -> bool:
        """True when running in local mode (SQLite + filesystem + in-process queue)."""
        return not self.database_url or self.database_url.startswith("sqlite")


settings = Settings()
