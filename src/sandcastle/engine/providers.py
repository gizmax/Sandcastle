"""Provider registry - maps model strings to runner configs, API keys, and pricing.

Supports Claude (default), MiniMax, OpenAI, Mistral, Google/Gemini (via
OpenRouter), Ollama, and oMLX (local Apple Silicon inference).  Claude models
use the Claude Agent SDK runner (runner.mjs), all others use the
OpenAI-compatible runner (runner-openai.mjs).
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelInfo:
    """Configuration for a single model."""

    provider: str  # "claude", "minimax", "openai", "google"
    api_model_id: str  # ID sent to the API
    runner: str  # "runner.mjs" | "runner-openai.mjs"
    api_key_env: str  # Environment variable name for the API key
    api_base_url: str | None  # Base URL (None = provider default)
    input_price_per_m: float  # USD per 1M input tokens
    output_price_per_m: float  # USD per 1M output tokens
    region: str = "us"  # Data residency region: "eu", "us", "local"


PROVIDER_REGISTRY: dict[str, ModelInfo] = {
    # Claude models (bare names for backward compatibility)
    "sonnet": ModelInfo(
        "claude", "sonnet", "runner.mjs",
        "ANTHROPIC_API_KEY", None, 3.0, 15.0, region="us",
    ),
    "opus": ModelInfo(
        "claude", "opus", "runner.mjs",
        "ANTHROPIC_API_KEY", None, 15.0, 75.0, region="us",
    ),
    "haiku": ModelInfo(
        "claude", "haiku", "runner.mjs",
        "ANTHROPIC_API_KEY", None, 0.80, 4.0, region="us",
    ),
    # MiniMax
    "minimax/m2.5": ModelInfo(
        "minimax", "MiniMax-M2.5", "runner-openai.mjs",
        "MINIMAX_API_KEY", "https://api.minimaxi.chat/v1", 0.30, 1.20, region="us",
    ),
    # OpenAI
    "openai/codex-mini": ModelInfo(
        "openai", "codex-mini", "runner-openai.mjs",
        "OPENAI_API_KEY", "https://api.openai.com/v1", 0.25, 2.0, region="us",
    ),
    "openai/codex": ModelInfo(
        "openai", "codex", "runner-openai.mjs",
        "OPENAI_API_KEY", "https://api.openai.com/v1", 1.25, 10.0, region="us",
    ),
    # Mistral (EU - French AI lab)
    "mistral/large": ModelInfo(
        "mistral", "mistral-large-latest", "runner-openai.mjs",
        "MISTRAL_API_KEY", "https://api.mistral.ai/v1", 2.0, 6.0, region="eu",
    ),
    "mistral/small": ModelInfo(
        "mistral", "mistral-small-latest", "runner-openai.mjs",
        "MISTRAL_API_KEY", "https://api.mistral.ai/v1", 0.10, 0.30, region="eu",
    ),
    "mistral/codestral": ModelInfo(
        "mistral", "codestral-latest", "runner-openai.mjs",
        "MISTRAL_API_KEY", "https://api.mistral.ai/v1", 0.30, 0.90, region="eu",
    ),
    # Google Gemini via OpenRouter
    "google/gemini-2.5-pro": ModelInfo(
        "google", "google/gemini-2.5-pro", "runner-openai.mjs",
        "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", 4.0, 20.0, region="us",
    ),
    # Ollama (local)
    "ollama": ModelInfo(
        "ollama", "llama3.2", "runner-openai.mjs",
        "", "http://localhost:11434/v1", 0.0, 0.0, region="local",
    ),
    # oMLX (local - OpenAI-compatible self-hosted LLM on Apple Silicon)
    "omlx/llama-4-scout": ModelInfo(
        "omlx", "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit", "runner-openai.mjs",
        "", "http://localhost:8080/v1", 0.0, 0.0, region="local",
    ),
    "omlx/mistral-small": ModelInfo(
        "omlx", "mlx-community/Mistral-Small-24B-Instruct-2501-4bit", "runner-openai.mjs",
        "", "http://localhost:8080/v1", 0.0, 0.0, region="local",
    ),
    "omlx/gemma-3": ModelInfo(
        "omlx", "mlx-community/gemma-3-27b-it-qat-4bit", "runner-openai.mjs",
        "", "http://localhost:8080/v1", 0.0, 0.0, region="local",
    ),
    "omlx/qwen-3": ModelInfo(
        "omlx", "mlx-community/Qwen3-30B-A3B-4bit", "runner-openai.mjs",
        "", "http://localhost:8080/v1", 0.0, 0.0, region="local",
    ),
    # NVIDIA NIM (local inference microservice, OpenAI-compatible) - e.g. served
    # on a DGX Spark / NVIDIA GPU. The base URL is read from settings.nim_base_url
    # at runtime (see resolve_base_url); these are common models, but any model a
    # NIM exposes works via the dynamic "nim/<model-id>" form (see resolve_model).
    "nim/llama-3.1-70b": ModelInfo(
        "nim", "meta/llama-3.1-70b-instruct", "runner-openai.mjs",
        "NIM_API_KEY", "http://localhost:8000/v1", 0.0, 0.0, region="local",
    ),
    "nim/llama-3.1-8b": ModelInfo(
        "nim", "meta/llama-3.1-8b-instruct", "runner-openai.mjs",
        "NIM_API_KEY", "http://localhost:8000/v1", 0.0, 0.0, region="local",
    ),
    "nim/mixtral-8x7b": ModelInfo(
        "nim", "mistralai/mixtral-8x7b-instruct-v0.1", "runner-openai.mjs",
        "NIM_API_KEY", "http://localhost:8000/v1", 0.0, 0.0, region="local",
    ),
    "nim/qwen2.5-coder-32b": ModelInfo(
        "nim", "qwen/qwen2.5-coder-32b-instruct", "runner-openai.mjs",
        "NIM_API_KEY", "http://localhost:8000/v1", 0.0, 0.0, region="local",
    ),
}

# All known model names (for validation)
KNOWN_MODELS = frozenset(PROVIDER_REGISTRY.keys())

# A dynamic NIM model id ("nim/<id>") must be a sane OpenAI/NGC-style model name -
# no whitespace, control chars, or path-traversal - so a crafted model string can't
# inject anything into the request or be mistaken for a path.
_NIM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$")


def _valid_nim_id(model_str: str) -> bool:
    """True if *model_str* is a well-formed dynamic ``nim/<id>`` model string."""
    if not (isinstance(model_str, str) and model_str.startswith("nim/")):
        return False
    rest = model_str[len("nim/"):]
    return bool(rest) and ".." not in rest and _NIM_ID_RE.match(rest) is not None


def _valid_adapter_id(model_str: str) -> bool:
    """True if *model_str* is a well-formed ``adapter/<id>`` (Self-Tune adapter)."""
    return (
        isinstance(model_str, str)
        and model_str.startswith("adapter/")
        and len(model_str) > len("adapter/")
    )


def resolve_model(model_str: str) -> ModelInfo:
    """Resolve a model string to its full configuration.

    Beyond the static registry, any ``nim/<model-id>`` resolves dynamically: a
    NIM exposes whatever model it serves over its OpenAI-compatible API, so we
    do not hardcode the catalogue. The base URL comes from settings.nim_base_url.

    Raises ``KeyError`` if the model is neither registered nor a valid nim/* id.
    """
    if model_str in PROVIDER_REGISTRY:
        return PROVIDER_REGISTRY[model_str]
    if _valid_nim_id(model_str):
        return ModelInfo(
            "nim", model_str[len("nim/"):], "runner-openai.mjs",
            "NIM_API_KEY", "http://localhost:8000/v1", 0.0, 0.0, region="local",
        )
    if _valid_adapter_id(model_str):
        # A locally-trained LoRA adapter (Overnight Self-Tune), served by the local
        # NIM/ollama. Authoritative existence check is the on-disk adapter registry.
        from sandcastle.engine.adapter_registry import AdapterRegistry

        if AdapterRegistry().get(model_str[len("adapter/"):]) is not None:
            return ModelInfo(
                "adapter", model_str[len("adapter/"):], "runner-openai.mjs",
                "", None, 0.0, 0.0, region="local",
            )
    raise KeyError(
        f"Unknown model '{model_str}'. "
        f"Available: {', '.join(sorted(PROVIDER_REGISTRY))}"
    )


def is_known_model(model_str: str) -> bool:
    """True if registered, a valid ``nim/<id>``, or an ``adapter/<id>`` string."""
    return model_str in KNOWN_MODELS or _valid_nim_id(model_str) or _valid_adapter_id(model_str)


# --- Spark Mode: auto-route the default model to a local NIM ------------------

# The local model the Spark auto-route targets (a static nim/* registry entry).
_SPARK_NIM_DEFAULT = "nim/llama-3.1-70b"
# Cache the reachability probe per base_url with a short TTL so the hot path is
# cheap but a NIM that starts after Sandcastle is still picked up.
_NIM_REACHABLE_CACHE: dict[str, tuple[bool, float]] = {}
_NIM_REACHABLE_TTL = 60.0


def is_nim_reachable(base_url: str | None = None, timeout_seconds: float | None = None) -> bool:
    """Fast, cached probe: does the NIM ``/v1/models`` endpoint answer? False on any error.

    A 2xx or 4xx (e.g. 401/403 - endpoint exists, auth required) counts as reachable;
    a 5xx, timeout, or connection error counts as not reachable. Result is cached per
    base_url for ~60s. Never raises.
    """
    from sandcastle.config import settings

    if base_url is None:
        base_url = settings.nim_base_url or "http://localhost:8000"
    base_url = base_url.rstrip("/")
    now = time.monotonic()
    cached = _NIM_REACHABLE_CACHE.get(base_url)
    if cached is not None and cached[1] > now:
        return cached[0]
    if timeout_seconds is None:
        timeout_seconds = max(0.1, settings.nim_probe_timeout_ms / 1000.0)
    ok = False
    try:
        import httpx

        resp = httpx.get(f"{base_url}/v1/models", timeout=timeout_seconds)
        ok = resp.status_code < 500
    except Exception:  # noqa: BLE001 - any probe failure means "not reachable"
        ok = False
    _NIM_REACHABLE_CACHE[base_url] = (ok, now + _NIM_REACHABLE_TTL)
    return ok


def maybe_spark_nim_route(model_str: str) -> str:
    """On a Spark with a reachable NIM, route the bare default model to the local NIM.

    Returns *model_str* unchanged unless ALL hold: spark_mode on, spark_nim_autoroute
    enabled, the step uses the bare default ``"sonnet"`` (explicit non-default models
    are always respected), data residency permits a local model, and the NIM answers.
    A NO-OP off-Spark, when disabled, or when NIM is unreachable - so it can never
    silently break a run.
    """
    if model_str != "sonnet":
        return model_str
    from sandcastle.config import settings

    if not (settings.spark_mode and settings.spark_nim_autoroute):
        return model_str
    if settings.data_residency == "eu":  # local NIM is region "local", not "eu"
        return model_str
    if not is_nim_reachable():
        return model_str
    return _SPARK_NIM_DEFAULT


def get_api_key(model_info: ModelInfo) -> str:
    """Read the API key for *model_info* from env / config.

    Falls back to the Settings object when the env var is empty.
    """
    # 1. Direct env var
    key = os.environ.get(model_info.api_key_env, "")
    if key:
        return key

    # 2. Settings fallback
    from sandcastle.config import settings

    attr_map = {
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "E2B_API_KEY": "e2b_api_key",
        "MINIMAX_API_KEY": "minimax_api_key",
        "OPENAI_API_KEY": "openai_api_key",
        "OPENROUTER_API_KEY": "openrouter_api_key",
        "MISTRAL_API_KEY": "mistral_api_key",
        "NIM_API_KEY": "nim_api_key",
    }
    attr = attr_map.get(model_info.api_key_env)
    if attr:
        return getattr(settings, attr, "")
    return ""


def resolve_base_url(model_info: ModelInfo) -> str:
    """Resolve the API base URL for *model_info*.

    For oMLX models the URL is read from ``settings.omlx_base_url`` at
    runtime so users can point to any oMLX server.  All other providers
    return their static ``api_base_url`` (or the OpenAI default).
    """
    if model_info.provider == "omlx":
        from sandcastle.config import settings
        base = settings.omlx_base_url or "http://localhost:8080"
        return base.rstrip("/") + "/v1"
    if model_info.provider == "nim":
        from sandcastle.config import settings
        base = settings.nim_base_url or "http://localhost:8000"
        return base.rstrip("/") + "/v1"
    if model_info.provider == "adapter":
        # Trained LoRA adapters are served by the local NIM (preferred) or Ollama.
        from sandcastle.config import settings
        base = settings.nim_base_url or settings.ollama_host or "http://localhost:8000"
        return base.rstrip("/") + "/v1"
    return model_info.api_base_url or "https://api.openai.com/v1"


def is_claude_model(model_str: str) -> bool:
    """Return True if *model_str* resolves to a Claude model."""
    info = PROVIDER_REGISTRY.get(model_str)
    return info is not None and info.provider == "claude"


# ---------------------------------------------------------------------------
# Failover chains
# ---------------------------------------------------------------------------

# Ordered fallback list for each model: same-provider cheaper first,
# then same-provider pricier, then cross-provider equivalents.
FAILOVER_CHAINS: dict[str, list[str]] = {
    "sonnet": [
        "haiku", "opus",
        "openai/codex-mini", "openai/codex", "minimax/m2.5", "google/gemini-2.5-pro",
        "mistral/large",
    ],
    "opus": [
        "sonnet", "haiku",
        "google/gemini-2.5-pro", "openai/codex",
        "mistral/large",
    ],
    "haiku": [
        "sonnet", "opus",
        "minimax/m2.5", "openai/codex-mini",
        "mistral/small", "mistral/codestral",
    ],
    "minimax/m2.5": [
        "openai/codex-mini", "haiku", "sonnet", "google/gemini-2.5-pro",
    ],
    "openai/codex-mini": [
        "openai/codex",
        "minimax/m2.5", "haiku", "sonnet",
        "mistral/small", "mistral/codestral",
    ],
    "openai/codex": [
        "openai/codex-mini",
        "sonnet", "opus", "google/gemini-2.5-pro",
        "mistral/large",
    ],
    "google/gemini-2.5-pro": [
        "sonnet", "opus",
        "openai/codex", "minimax/m2.5",
    ],
    # Mistral EU models - prefer same-provider, then cross-provider
    "mistral/large": [
        "mistral/small", "mistral/codestral",
        "sonnet", "opus", "openai/codex",
    ],
    "mistral/small": [
        "mistral/large", "mistral/codestral",
        "haiku", "openai/codex-mini",
    ],
    "mistral/codestral": [
        "mistral/small", "mistral/large",
        "openai/codex-mini", "haiku",
    ],
    # Ollama local - no cloud alternatives in local mode
    "ollama": [],
    # oMLX local - failover to other oMLX models, then Ollama
    "omlx/llama-4-scout": [
        "omlx/mistral-small", "omlx/qwen-3", "omlx/gemma-3", "ollama",
    ],
    "omlx/mistral-small": [
        "omlx/llama-4-scout", "omlx/qwen-3", "omlx/gemma-3", "ollama",
    ],
    "omlx/gemma-3": [
        "omlx/mistral-small", "omlx/llama-4-scout", "omlx/qwen-3", "ollama",
    ],
    "omlx/qwen-3": [
        "omlx/llama-4-scout", "omlx/mistral-small", "omlx/gemma-3", "ollama",
    ],
    # NVIDIA NIM local - failover to other NIM models, then Ollama.
    "nim/llama-3.1-70b": ["nim/llama-3.1-8b", "nim/mixtral-8x7b", "ollama"],
    "nim/llama-3.1-8b": ["nim/llama-3.1-70b", "nim/mixtral-8x7b", "ollama"],
    "nim/mixtral-8x7b": ["nim/llama-3.1-70b", "nim/llama-3.1-8b", "ollama"],
    "nim/qwen2.5-coder-32b": ["nim/llama-3.1-70b", "nim/mixtral-8x7b", "ollama"],
}


class ProviderFailover:
    """Thread-safe failover manager with per-key cooldown tracking.

    Differentiates between rate-limit (429) and server-error (5xx) cooldowns:
    rate-limit cooldowns default to shorter duration since they typically
    clear faster. The ``rate_limit_cooldown_seconds`` can be configured
    separately.
    """

    def __init__(
        self,
        *,
        rate_limit_cooldown_seconds: float | None = None,
    ) -> None:
        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()
        self._rate_limit_cooldown = rate_limit_cooldown_seconds

    @property
    def rate_limit_cooldown_seconds(self) -> float:
        """Return the cooldown for 429 rate-limit errors (shorter than 5xx)."""
        if self._rate_limit_cooldown is not None:
            return self._rate_limit_cooldown
        from sandcastle.config import settings
        # Use 1/4 of the normal cooldown for rate limits (min 10s)
        return max(10.0, settings.failover_cooldown_seconds / 4)

    def mark_cooldown(
        self,
        api_key_env: str,
        duration_seconds: float | None = None,
        *,
        is_rate_limit: bool = False,
    ) -> None:
        """Put *api_key_env* on cooldown until ``now + duration_seconds``.

        If *duration_seconds* is None, uses the config default. If
        *is_rate_limit* is True, uses a shorter cooldown appropriate for
        429 errors.
        """
        if duration_seconds is None:
            if is_rate_limit:
                duration_seconds = self.rate_limit_cooldown_seconds
            else:
                from sandcastle.config import settings
                duration_seconds = settings.failover_cooldown_seconds
        with self._lock:
            self._cooldowns[api_key_env] = time.monotonic() + duration_seconds

    def is_available(self, api_key_env: str) -> bool:
        """Return True if *api_key_env* is NOT on cooldown."""
        with self._lock:
            deadline = self._cooldowns.get(api_key_env)
            if deadline is None:
                return True
            if time.monotonic() >= deadline:
                del self._cooldowns[api_key_env]
                return True
            return False

    def clear_cooldown(self, api_key_env: str) -> None:
        """Remove cooldown for *api_key_env* immediately."""
        with self._lock:
            self._cooldowns.pop(api_key_env, None)

    def get_alternatives(self, model_str: str, *, data_residency: str = "") -> list[str]:
        """Return ordered fallback models, filtered by cooldown and configured keys.

        When *data_residency* is set (e.g. "eu" or "local"), only models whose
        region matches the requirement are returned. If no alternatives satisfy
        the residency constraint the caller should fail rather than cross the
        data-border.
        """
        chain = FAILOVER_CHAINS.get(model_str, [])
        result: list[str] = []
        for alt in chain:
            info = PROVIDER_REGISTRY.get(alt)
            if info is None:
                continue
            # Skip if residency constraint is active and region doesn't match
            if data_residency and info.region != data_residency:
                continue
            # Skip if key is on cooldown
            if not self.is_available(info.api_key_env):
                continue
            # Skip if key is not configured (local providers need no key)
            if info.api_key_env and not get_api_key(info):
                continue
            result.append(alt)
        return result

    def get_status(self) -> dict[str, Any]:
        """Return diagnostics: active cooldowns and available models."""
        now = time.monotonic()
        with self._lock:
            active_cooldowns: dict[str, float] = {}
            for key, deadline in list(self._cooldowns.items()):
                remaining = deadline - now
                if remaining > 0:
                    active_cooldowns[key] = round(remaining, 1)
                else:
                    del self._cooldowns[key]

        available: list[str] = []
        unavailable: list[str] = []
        for model_str, info in PROVIDER_REGISTRY.items():
            has_key = not info.api_key_env or bool(get_api_key(info))
            if has_key and self.is_available(info.api_key_env):
                available.append(model_str)
            else:
                unavailable.append(model_str)

        return {
            "active_cooldowns": active_cooldowns,
            "available_models": available,
            "unavailable_models": unavailable,
        }


# Singleton
_failover_instance: ProviderFailover | None = None
_failover_lock = threading.Lock()


def get_failover() -> ProviderFailover:
    """Return the shared ``ProviderFailover`` singleton."""
    global _failover_instance
    if _failover_instance is None:
        with _failover_lock:
            if _failover_instance is None:
                _failover_instance = ProviderFailover()
    return _failover_instance
