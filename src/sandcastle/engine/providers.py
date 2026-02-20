"""Provider registry - maps model strings to runner configs, API keys, and pricing.

Supports Claude (default), MiniMax, OpenAI, and OpenRouter-based models.
Claude models use the Claude Agent SDK runner (runner.mjs), all others use
the OpenAI-compatible runner (runner-openai.mjs).
"""

from __future__ import annotations

import os
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


PROVIDER_REGISTRY: dict[str, ModelInfo] = {
    # Claude models (bare names for backward compatibility)
    "sonnet": ModelInfo(
        "claude", "sonnet", "runner.mjs",
        "ANTHROPIC_API_KEY", None, 3.0, 15.0,
    ),
    "opus": ModelInfo(
        "claude", "opus", "runner.mjs",
        "ANTHROPIC_API_KEY", None, 15.0, 75.0,
    ),
    "haiku": ModelInfo(
        "claude", "haiku", "runner.mjs",
        "ANTHROPIC_API_KEY", None, 0.80, 4.0,
    ),
    # MiniMax
    "minimax/m2.5": ModelInfo(
        "minimax", "MiniMax-M2.5", "runner-openai.mjs",
        "MINIMAX_API_KEY", "https://api.minimaxi.chat/v1", 0.30, 1.20,
    ),
    # OpenAI
    "openai/codex-mini": ModelInfo(
        "openai", "codex-mini", "runner-openai.mjs",
        "OPENAI_API_KEY", "https://api.openai.com/v1", 0.25, 2.0,
    ),
    "openai/codex": ModelInfo(
        "openai", "codex", "runner-openai.mjs",
        "OPENAI_API_KEY", "https://api.openai.com/v1", 1.25, 10.0,
    ),
    # Google Gemini via OpenRouter
    "google/gemini-2.5-pro": ModelInfo(
        "google", "google/gemini-2.5-pro", "runner-openai.mjs",
        "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", 4.0, 20.0,
    ),
}

# All known model names (for validation)
KNOWN_MODELS = frozenset(PROVIDER_REGISTRY.keys())


def resolve_model(model_str: str) -> ModelInfo:
    """Resolve a model string to its full configuration.

    Raises ``KeyError`` if the model is not in the registry.
    """
    if model_str not in PROVIDER_REGISTRY:
        raise KeyError(
            f"Unknown model '{model_str}'. "
            f"Available: {', '.join(sorted(PROVIDER_REGISTRY))}"
        )
    return PROVIDER_REGISTRY[model_str]


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
    }
    attr = attr_map.get(model_info.api_key_env)
    if attr:
        return getattr(settings, attr, "")
    return ""


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
        "openai/codex-mini", "minimax/m2.5", "google/gemini-2.5-pro",
    ],
    "opus": [
        "sonnet", "haiku",
        "google/gemini-2.5-pro", "openai/codex",
    ],
    "haiku": [
        "sonnet", "opus",
        "minimax/m2.5", "openai/codex-mini",
    ],
    "minimax/m2.5": [
        "openai/codex-mini", "haiku", "sonnet",
    ],
    "openai/codex-mini": [
        "openai/codex",
        "minimax/m2.5", "haiku", "sonnet",
    ],
    "openai/codex": [
        "openai/codex-mini",
        "sonnet", "google/gemini-2.5-pro",
    ],
    "google/gemini-2.5-pro": [
        "sonnet", "opus",
        "openai/codex", "minimax/m2.5",
    ],
}


class ProviderFailover:
    """Thread-safe failover manager with per-key cooldown tracking."""

    def __init__(self) -> None:
        self._cooldowns: dict[str, float] = {}
        self._lock = threading.Lock()

    def mark_cooldown(
        self,
        api_key_env: str,
        duration_seconds: float | None = None,
    ) -> None:
        """Put *api_key_env* on cooldown until ``now + duration_seconds``."""
        if duration_seconds is None:
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

    def get_alternatives(self, model_str: str) -> list[str]:
        """Return ordered fallback models, filtered by cooldown and configured keys."""
        chain = FAILOVER_CHAINS.get(model_str, [])
        result: list[str] = []
        for alt in chain:
            info = PROVIDER_REGISTRY.get(alt)
            if info is None:
                continue
            # Skip if key is on cooldown
            if not self.is_available(info.api_key_env):
                continue
            # Skip if key is not configured
            if not get_api_key(info):
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
            if get_api_key(info) and self.is_available(info.api_key_env):
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
