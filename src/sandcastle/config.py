"""Application configuration loaded from environment variables."""

import logging
import os
from pathlib import Path

from pydantic import AliasChoices, Field, computed_field, field_validator
from pydantic_settings import BaseSettings

from sandcastle.engine.spark import get_spark_info

_logger = logging.getLogger(__name__)

_VALID_SANDBOX_BACKENDS = frozenset({"e2b", "docker", "local", "cloudflare"})
_VALID_STORAGE_BACKENDS = frozenset({"local", "s3"})
_VALID_MEMORY_BACKENDS = frozenset({"local", "cloud"})
_VALID_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
_VALID_UPDATE_CHANNELS = frozenset({"stable", "beta", "pin"})

_DEFAULT_DATA_DIR = str(Path.home() / ".sandcastle" / "data")
_DEFAULT_WORKFLOWS_DIR = str(Path.home() / ".sandcastle" / "workflows")


class Settings(BaseSettings):
    """Sandcastle configuration."""

    # Runtime connection
    anthropic_api_key: str = ""
    e2b_api_key: str = ""

    # Multi-model provider keys (optional)
    minimax_api_key: str = ""
    mistral_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""

    # oMLX server URL (OpenAI-compatible local inference on Apple Silicon)
    omlx_base_url: str = "http://localhost:8080"

    # Ollama server URL (for GLM-OCR and other local models)
    ollama_host: str = "http://localhost:11434"

    # NVIDIA NIM server URL (OpenAI-compatible inference microservice, e.g. on a
    # DGX Spark / NVIDIA GPU). nim_api_key is optional (local NIMs need none;
    # NGC-hosted NIMs do). Used by the nim/* provider in engine/providers.py.
    nim_base_url: str = "http://localhost:8000"
    nim_api_key: str = ""

    # Sampling temperature for LLM/standard steps. Workflow steps do structured,
    # deterministic work, so a LOW default is essential: some OpenAI-compatible
    # endpoints (e.g. NVIDIA-hosted models) default to temperature 1.0, which
    # produces garbled/hallucinated output. Pin a low value; override per step
    # via the step's llm_config.temperature.
    step_temperature: float = 0.2

    # Spark Mode: on a detected DGX Spark, auto-route default-model LLM steps to the
    # local NIM when it is reachable ($0, on-box). Opt out with SANDCASTLE_SPARK_NIM_-
    # AUTOROUTE=false. nim_probe_timeout_ms bounds the reachability probe.
    spark_nim_autoroute: bool = True
    nim_probe_timeout_ms: int = 2000
    # Model the autoroute sends bare default steps to. Must be a model id the local
    # OpenAI-compatible server actually serves - e.g. "nim/ornith" for a vLLM started
    # with --served-model-name ornith. The reachability probe only checks /v1/models
    # answers, not that this model exists on it.
    spark_nim_default_model: str = "nim/llama-3.1-70b"

    # Global default model for workflow steps that don't set an explicit `model:`.
    # Empty = the built-in default ("sonnet", plus Spark autoroute when applicable).
    # Settable at runtime via PATCH /api/settings (validated against resolve_model)
    # and picked in the onboarding wizard from detected local providers.
    workflow_default_model: str = ""

    # Overnight Self-Tune: the evolution loop may add a LoRA fine-tune mutation that
    # trains a local adapter on a workflow's own eval data and A/B-promotes it. Off by
    # default; the trainer is a deterministic mock unless trainer_backend="gpu" (real
    # SFT - needs the [training] extras + a CUDA GPU, e.g. a DGX Spark). See
    # engine/training/ and docs/overnight-self-tune-spark.md.
    evolution_job_timeout: int = Field(default=3600, ge=60, le=86400)
    evolution_auto_finetune: bool = False
    evolution_finetune_min_samples: int = 10
    trainer_backend: str = "mock"  # "mock" | "gpu"
    lora_r: int = 8
    lora_alpha: int = 16
    lora_lr: float = 1e-4
    lora_epochs: int = 3
    lora_dropout: float = 0.05
    lora_max_steps: int = 0  # 0 = no step cap (epochs decide)
    lora_seed: int = 42
    lora_batch_size: int = 1
    lora_grad_accum: int = 8
    lora_max_seq_len: int = 2048
    # HF model the GPU trainer actually fine-tunes (the adapter must be served over the
    # same base by vLLM/NIM). Default is small enough for bf16 LoRA on a 128 GB Spark;
    # for 70B-class bases set lora_quantize="4bit"/"8bit" (needs bitsandbytes with
    # CUDA aarch64 wheels - not guaranteed on DGX OS, hence opt-in).
    lora_base_model_id: str = "Qwen/Qwen2.5-7B-Instruct"
    lora_quantize: str = "none"  # "none" | "8bit" | "4bit"
    lora_output_dir: str = ""  # empty = the adapter registry dir (~/.sandcastle/adapters)

    # Self-Healing Workflows: a nightly pass scans unresolved dead-letter items,
    # asks the advisor LLM for a minimal patch, files it as a draft workflow version
    # behind an approval request. healer_auto_apply publishes high-confidence patches
    # (>= healer_confidence_threshold) directly. Off by default; opt in explicitly.
    healer_enabled: bool = False
    healer_auto_apply: bool = False
    healer_confidence_threshold: float = 0.8
    healer_max_attempts: int = 2
    healer_lookback_hours: int = 168  # how far back to scan for unresolved failures

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

    # Allow non-admin tenants to run in-process "code" steps. Off by default so
    # multi-tenant deployments stay safe; single-tenant self-hosted operators who
    # intentionally use code steps can opt in via CODE_STEPS_ALLOW_UNTRUSTED=true.
    code_steps_allow_untrusted: bool = False

    # Run "code" steps in a separate Python subprocess (out-of-process isolation)
    # so a sandbox escape cannot reach the parent process memory (settings, DB
    # session factory, other tenants' data). On by default. Operators can fall
    # back to the legacy in-process path via CODE_STEPS_OUT_OF_PROCESS=false if
    # the subprocess path misbehaves in their environment.
    code_steps_out_of_process: bool = True

    # Fail closed when the subprocess isolation path is unavailable. Operators
    # may explicitly opt into the legacy in-process fallback for a controlled,
    # single-tenant environment.
    code_steps_allow_inprocess_fallback: bool = False

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
    allow_insecure_bind: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "SANDCASTLE_ALLOW_INSECURE_BIND", "ALLOW_INSECURE_BIND", "allow_insecure_bind"
        ),
    )
    dashboard_origin: str = "http://localhost:5173"

    # Budget
    default_max_cost_usd: float = 0.0  # 0 = no limit (must be >= 0)

    # The Architect - autonomous generate->run->evaluate->refine loop
    architect_max_iterations: int = 3  # loop bound per session
    architect_budget_usd: float = 1.0  # total live-run spend cap per session
    architect_score_threshold: float = 0.7  # minimum LLM-judge score to accept

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

    # Model Time Machine: judge model used to score old vs new outputs on live
    # replays (any provider-registry model string; default = cheap Claude tier)
    timemachine_judge_model: str = "haiku"

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
    tool_smtp_port: int = 587
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

    # Browser backends
    lightpanda_path: str = ""  # Path to lightpanda binary (empty = use PATH)
    browserbase_api_key: str = ""  # Browserbase cloud browser API key
    browserbase_project_id: str = ""  # Browserbase project ID (optional)

    # License
    license_key: str = ""  # Ed25519-signed license key (sc_lic_...)

    # Telemetry (opt-in error reporting)
    telemetry_enabled: bool = False  # Set to True to send error reports via Sentry
    sentry_dsn: str = ""  # Sentry DSN - get one free at sentry.io

    # Privacy Router (PII redaction)
    privacy_enabled: bool = False
    privacy_entities: str = "email,phone,ssn,credit_card"  # comma-separated entity types
    privacy_apply_to: str = "outputs,webhooks"  # comma-separated apply_to targets

    # Compliance mode:
    #   ""          - disabled
    #   "eu_ai_act" - EU AI Act enforcement (high-risk approval gates, etc.)
    #   "black_box" - flight-recorder mode: every run is recorded to a signed,
    #                 tamper-evident cassette; requires data_residency=local and
    #                 a configured audit_key, and run responses expose the
    #                 signed chain head. Verify with `sandcastle audit verify`.
    compliance_mode: str = ""

    # Secret key for signing audit chains (HMAC-SHA256). Set via the
    # SANDCASTLE_AUDIT_KEY (or AUDIT_KEY) env var - never hardcode it.
    audit_key: str = Field(
        default="",
        validation_alias=AliasChoices("SANDCASTLE_AUDIT_KEY", "AUDIT_KEY", "audit_key"),
    )

    # Data residency: "" = no restriction, "eu" = EU only, "local" = local/on-prem only
    data_residency: str = ""  # "", "eu", "local"

    # Advisor quality mode for SLO-aware routing:
    #   "auto"           - pick model tier by purpose (generation=high, judge=low, etc.)
    #   "always_best"    - always use highest-quality model regardless of purpose
    #   "always_cheapest"- always use cheapest model regardless of purpose
    advisor_quality_mode: str = "auto"

    # OpenTelemetry (distributed tracing)
    otel_enabled: bool = False  # Set to True to enable OTLP trace export
    otel_endpoint: str = ""  # OTLP HTTP endpoint, e.g. "http://localhost:4318"
    otel_service_name: str = "sandcastle"  # Service name reported in traces

    # Verified template marketplace - static index of .sctpl bundles used by
    # `sandcastle template search`. Point this at any static index.json you host.
    template_index_url: str = (
        "https://raw.githubusercontent.com/gizmax/Sandcastle/main/hub/template-index.json"
    )

    # Auto-update settings
    update_channel: str = "stable"  # "stable" | "beta" | "pin"
    pinned_version: str = ""  # only used with "pin" channel
    auto_update_check: bool = True  # check for updates on startup
    update_blackout_start: str = ""  # e.g. "22:00" - no updates during this window
    update_blackout_end: str = ""  # e.g. "06:00"
    update_approval_required: bool = False  # enterprise: admin must approve

    # Sandcastle Mesh: multiple machines forming one orchestration mesh.
    # The coordinator routes steps with `requires: [...]` to nodes whose
    # capability manifest satisfies ALL required capabilities.
    mesh_enabled: bool = False
    mesh_token: str = ""  # shared secret for node registration/heartbeat/execution
    mesh_heartbeat_seconds: int = 15  # node heartbeat interval; dead after 3 missed beats

    # Logging
    log_level: str = "info"

    @field_validator("sandbox_backend", mode="after")
    @classmethod
    def _validate_sandbox_backend(cls, v: str) -> str:
        """Validate sandbox_backend against known backends."""
        v = v.strip().lower()
        if v not in _VALID_SANDBOX_BACKENDS:
            _logger.warning(
                "Unknown SANDBOX_BACKEND '%s', falling back to 'e2b'. "
                "Valid options: %s",
                v,
                ", ".join(sorted(_VALID_SANDBOX_BACKENDS)),
            )
            return "e2b"
        return v

    @field_validator("storage_backend", mode="after")
    @classmethod
    def _validate_storage_backend(cls, v: str) -> str:
        """Validate storage_backend against known backends."""
        v = v.strip().lower()
        if v not in _VALID_STORAGE_BACKENDS:
            _logger.warning(
                "Unknown STORAGE_BACKEND '%s', falling back to 'local'. "
                "Valid options: %s",
                v,
                ", ".join(sorted(_VALID_STORAGE_BACKENDS)),
            )
            return "local"
        return v

    @field_validator("memory_backend", mode="after")
    @classmethod
    def _validate_memory_backend(cls, v: str) -> str:
        """Validate memory_backend against known backends."""
        v = v.strip().lower()
        if v not in _VALID_MEMORY_BACKENDS:
            _logger.warning(
                "Unknown MEMORY_BACKEND '%s', falling back to 'local'. "
                "Valid options: %s",
                v,
                ", ".join(sorted(_VALID_MEMORY_BACKENDS)),
            )
            return "local"
        return v

    @field_validator("mesh_heartbeat_seconds", mode="after")
    @classmethod
    def _validate_mesh_heartbeat(cls, v: int) -> int:
        """Ensure mesh_heartbeat_seconds is at least 1."""
        if v < 1:
            _logger.warning(
                "MESH_HEARTBEAT_SECONDS=%d is invalid (must be >= 1), using 15", v
            )
            return 15
        return v

    @field_validator("log_level", mode="after")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """Validate log_level against known levels."""
        v = v.strip().lower()
        if v not in _VALID_LOG_LEVELS:
            _logger.warning(
                "Unknown LOG_LEVEL '%s', falling back to 'info'. "
                "Valid options: %s",
                v,
                ", ".join(sorted(_VALID_LOG_LEVELS)),
            )
            return "info"
        return v

    @field_validator("update_channel", mode="after")
    @classmethod
    def _validate_update_channel(cls, v: str) -> str:
        """Validate update_channel against known channels."""
        v = v.strip().lower()
        if v not in _VALID_UPDATE_CHANNELS:
            _logger.warning(
                "Unknown UPDATE_CHANNEL '%s', falling back to 'stable'. "
                "Valid options: %s",
                v,
                ", ".join(sorted(_VALID_UPDATE_CHANNELS)),
            )
            return "stable"
        return v

    @field_validator("max_concurrent_sandboxes", mode="after")
    @classmethod
    def _validate_max_concurrent(cls, v: int) -> int:
        """Ensure max_concurrent_sandboxes is between 1 and 50.

        Spark Mode auto-config: on a detected DGX Spark, bump the default (5) to
        40 so wide fan-outs use the box's headroom - unless the operator set
        MAX_CONCURRENT_SANDBOXES explicitly (then their value is respected).
        """
        if v == 5 and not os.getenv("MAX_CONCURRENT_SANDBOXES"):
            try:
                if get_spark_info().is_spark:
                    v = 40
            except Exception:  # noqa: BLE001 - detection must never break config load
                pass
        if v < 1:
            _logger.warning(
                "MAX_CONCURRENT_SANDBOXES=%d is invalid (must be >= 1), "
                "using 1",
                v,
            )
            return 1
        if v > 50:
            _logger.warning(
                "MAX_CONCURRENT_SANDBOXES=%d exceeds maximum (50), "
                "clamping to 50 to prevent resource exhaustion",
                v,
            )
            return 50
        return v

    @field_validator("default_max_cost_usd", mode="after")
    @classmethod
    def _validate_default_max_cost(cls, v: float) -> float:
        """Ensure default_max_cost_usd is non-negative (0 = no limit)."""
        if v < 0:
            _logger.warning(
                "DEFAULT_MAX_COST_USD=%.2f is invalid (must be >= 0), "
                "using 0.0 (no limit)",
                v,
            )
            return 0.0
        return v

    @field_validator("architect_max_iterations", mode="after")
    @classmethod
    def _validate_architect_max_iterations(cls, v: int) -> int:
        """Ensure architect_max_iterations is at least 1."""
        if v < 1:
            _logger.warning(
                "ARCHITECT_MAX_ITERATIONS=%d is invalid (must be >= 1), using 3", v
            )
            return 3
        return v

    @field_validator("architect_budget_usd", mode="after")
    @classmethod
    def _validate_architect_budget(cls, v: float) -> float:
        """Ensure architect_budget_usd is positive."""
        if v <= 0:
            _logger.warning(
                "ARCHITECT_BUDGET_USD=%.2f is invalid (must be > 0), using 1.0", v
            )
            return 1.0
        return v

    @field_validator("architect_score_threshold", mode="after")
    @classmethod
    def _validate_architect_threshold(cls, v: float) -> float:
        """Clamp architect_score_threshold to [0.0, 1.0]."""
        if v < 0.0 or v > 1.0:
            clamped = max(0.0, min(1.0, v))
            _logger.warning(
                "ARCHITECT_SCORE_THRESHOLD=%.2f is out of range [0.0, 1.0], "
                "clamping to %.2f",
                v,
                clamped,
            )
            return clamped
        return v

    @field_validator("tool_smtp_port", mode="after")
    @classmethod
    def _validate_tool_smtp_port(cls, v: int) -> int:
        """Ensure tool_smtp_port is a valid port number."""
        if v < 1 or v > 65535:
            _logger.warning(
                "TOOL_SMTP_PORT=%d is invalid (must be 1-65535), using 587",
                v,
            )
            return 587
        return v

    @field_validator("memory_admit_threshold", mode="after")
    @classmethod
    def _validate_admit_threshold(cls, v: float) -> float:
        """Clamp memory_admit_threshold to [0.0, 1.0]."""
        if v < 0.0 or v > 1.0:
            clamped = max(0.0, min(1.0, v))
            _logger.warning(
                "MEMORY_ADMIT_THRESHOLD=%.2f is out of range [0.0, 1.0], "
                "clamping to %.2f",
                v,
                clamped,
            )
            return clamped
        return v

    @field_validator("memory_max_age_days", mode="after")
    @classmethod
    def _validate_max_age_days(cls, v: int) -> int:
        """Ensure memory_max_age_days is non-negative."""
        if v < 0:
            _logger.warning(
                "MEMORY_MAX_AGE_DAYS=%d is invalid (must be >= 0), "
                "using 0 (no expiry)",
                v,
            )
            return 0
        return v

    @field_validator("failover_cooldown_seconds", mode="after")
    @classmethod
    def _validate_failover_cooldown(cls, v: float) -> float:
        """Ensure failover_cooldown_seconds is positive."""
        if v <= 0:
            _logger.warning(
                "FAILOVER_COOLDOWN_SECONDS=%.1f is invalid (must be > 0), "
                "using 60.0",
                v,
            )
            return 60.0
        return v

    @field_validator("docker_pids_limit", mode="after")
    @classmethod
    def _validate_docker_pids_limit(cls, v: int) -> int:
        """Ensure docker_pids_limit is at least 1."""
        if v < 1:
            _logger.warning(
                "DOCKER_PIDS_LIMIT=%d is invalid (must be >= 1), using 100",
                v,
            )
            return 100
        return v

    @field_validator("docker_cpu_period", mode="after")
    @classmethod
    def _validate_docker_cpu_period(cls, v: int) -> int:
        """Ensure docker_cpu_period is positive."""
        if v < 1000:
            _logger.warning(
                "DOCKER_CPU_PERIOD=%d is invalid (must be >= 1000), "
                "using 100000",
                v,
            )
            return 100_000
        return v

    @field_validator("docker_cpu_quota", mode="after")
    @classmethod
    def _validate_docker_cpu_quota(cls, v: int) -> int:
        """Ensure docker_cpu_quota is positive."""
        if v < 1000:
            _logger.warning(
                "DOCKER_CPU_QUOTA=%d is invalid (must be >= 1000), "
                "using 50000",
                v,
            )
            return 50_000
        return v

    @field_validator("key_rotation_grace_hours", mode="after")
    @classmethod
    def _validate_key_rotation_grace(cls, v: int) -> int:
        """Ensure key_rotation_grace_hours is non-negative."""
        if v < 0:
            _logger.warning(
                "KEY_ROTATION_GRACE_HOURS=%d is invalid (must be >= 0), "
                "using 24",
                v,
            )
            return 24
        return v

    @field_validator("max_workflow_depth", mode="after")
    @classmethod
    def _validate_max_workflow_depth(cls, v: int) -> int:
        """Ensure max_workflow_depth is between 1 and 20."""
        if v < 1:
            _logger.warning(
                "MAX_WORKFLOW_DEPTH=%d is invalid (must be >= 1), using 5",
                v,
            )
            return 5
        if v > 20:
            _logger.warning(
                "MAX_WORKFLOW_DEPTH=%d exceeds maximum (20), clamping to 20 "
                "to prevent stack overflow in recursive workflows",
                v,
            )
            return 20
        return v

    @field_validator("redis_url", mode="after")
    @classmethod
    def _validate_redis_url(cls, v: str) -> str:
        """Validate redis_url scheme when set (empty = in-process queue)."""
        v = v.strip()
        if not v:
            return ""
        _valid_redis_schemes = ("redis://", "rediss://", "unix://")
        if not any(v.startswith(s) for s in _valid_redis_schemes):
            _logger.warning(
                "REDIS_URL '%s' has invalid scheme. "
                "Expected one of: %s. Falling back to empty (in-process queue).",
                v[:30] + ("..." if len(v) > 30 else ""),
                ", ".join(_valid_redis_schemes),
            )
            return ""
        return v

    @field_validator("update_blackout_start", "update_blackout_end", mode="after")
    @classmethod
    def _validate_blackout_time(cls, v: str) -> str:
        """Validate HH:MM format for update blackout window times."""
        v = v.strip()
        if not v:
            return ""
        parts = v.split(":")
        if len(parts) != 2:
            _logger.warning(
                "Invalid blackout time '%s' (expected HH:MM format), ignoring", v
            )
            return ""
        try:
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("out of range")
        except (ValueError, TypeError):
            _logger.warning(
                "Invalid blackout time '%s' (expected HH:MM with valid hour/minute), ignoring", v
            )
            return ""
        return v

    @field_validator("dashboard_origin", mode="after")
    @classmethod
    def _validate_dashboard_origin(cls, v: str) -> str:
        """Strip whitespace and trailing slashes from dashboard_origin."""
        v = v.strip().rstrip("/")
        return v

    @field_validator("data_dir", "workflows_dir", mode="after")
    @classmethod
    def _expand_home(cls, v: str, info) -> str:
        """Expand ~ and resolve empty/relative paths to absolute.

        Empty string is treated as the field default to prevent the data
        directory accidentally pointing at the process working directory.
        """
        if not v:
            # Fall back to field default when empty string is provided.
            default = cls.model_fields[info.field_name].default
            if default:
                v = default
            else:
                v = str(Path.home() / ".sandcastle")
        p = Path(v).expanduser()
        # Resolve relative paths to absolute so data does not silently
        # land in whatever the current working directory happens to be.
        if not p.is_absolute():
            p = p.resolve()
        return str(p)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Fields that contain secrets and must be redacted in safe_dump / logs
    _SENSITIVE_FIELDS: frozenset[str] = frozenset({
        "anthropic_api_key", "e2b_api_key", "openai_api_key",
        "openrouter_api_key", "minimax_api_key", "mistral_api_key", "sentry_dsn",
        "admin_api_key", "webhook_secret", "credential_encryption_key", "audit_key",
        "aws_access_key_id", "aws_secret_access_key",
        "database_url", "redis_url", "license_key",
        "tool_slack_bot_token", "tool_jira_api_token",
        "tool_github_token", "tool_notion_api_key",
        "tool_hubspot_api_key", "tool_salesforce_client_id",
        "tool_salesforce_client_secret", "tool_salesforce_refresh_token",
        "tool_zendesk_api_token", "tool_smtp_password",
        "tool_google_service_account", "tool_teams_webhook_url",
        "tool_postgresql_url",
        "browserbase_api_key",
        "nim_api_key",
        "mesh_token",
    })

    def safe_dump(self) -> dict:
        """Return settings dict with sensitive values redacted.

        Use this instead of model_dump() when exposing settings in logs,
        debug endpoints, or error messages to prevent credential leakage.
        """
        data = self.model_dump()
        for key in self._SENSITIVE_FIELDS:
            if key in data and data[key]:
                data[key] = "***"
        return data

    @computed_field
    @property
    def is_local_mode(self) -> bool:
        """True when running in local mode (SQLite + filesystem + in-process queue)."""
        return not self.database_url or self.database_url.startswith("sqlite")

    @computed_field
    @property
    def spark_mode(self) -> bool:
        """True when running on a DGX Spark (and Spark Mode is not disabled).

        SANDCASTLE_SPARK_MODE=on|off overrides hardware auto-detection. This is the
        single source of truth other features read; they never re-detect hardware.
        """
        override = os.getenv("SANDCASTLE_SPARK_MODE", "").strip().lower()
        if override in ("on", "true", "1", "yes"):
            return True
        if override in ("off", "false", "0", "no"):
            return False
        try:
            return get_spark_info().is_spark
        except Exception:  # noqa: BLE001 - detection must never break config access
            return False


settings = Settings()


_LOOPBACK_BIND_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def validate_server_bind(host: str, config: Settings | None = None) -> None:
    """Refuse unauthenticated network binds unless explicitly opted out."""
    active_config = config or settings
    if (
        host.strip().lower() in _LOOPBACK_BIND_HOSTS
        or active_config.auth_required
        or active_config.allow_insecure_bind
    ):
        return
    raise RuntimeError(
        "Refusing to bind to a non-loopback host while AUTH_REQUIRED=false. "
        "Set AUTH_REQUIRED=true or explicitly set SANDCASTLE_ALLOW_INSECURE_BIND=true."
    )
