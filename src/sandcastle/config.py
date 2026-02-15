"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Sandcastle configuration."""

    # Sandstorm connection
    sandstorm_url: str = "http://localhost:8000"
    anthropic_api_key: str = ""
    e2b_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://sandcastle:sandcastle@localhost:5432/sandcastle"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Storage
    storage_backend: str = "s3"  # "s3" or "local"
    storage_bucket: str = "sandcastle-data"
    storage_endpoint: str = "http://localhost:9000"
    aws_access_key_id: str = "minioadmin"
    aws_secret_access_key: str = "minioadmin"

    # Webhooks
    webhook_secret: str = "your-webhook-signing-secret"

    # Auth
    auth_required: bool = False  # Set to True to enforce API key auth
    dashboard_origin: str = "http://localhost:5173"

    # Workflows directory
    workflows_dir: str = "./workflows"

    # Logging
    log_level: str = "info"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
