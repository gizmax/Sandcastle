"""Restore dashboard-managed settings from the database into the runtime config.

Shared by the API lifespan (main.py) and the arq worker startup: without the
worker-side call, settings saved via PATCH /api/settings (provider API keys,
workflow_default_model, ...) only ever applied to the API process, so workflow
steps executed by the worker ran with the container's env defaults instead of
what the user configured in the dashboard.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Only restore settings that are safe to change at runtime. Security-critical
# settings (auth, encryption, DB, Redis) must come from environment variables
# and cannot be overridden from the DB.
_RESTORABLE_SETTINGS = {
    "anthropic_api_key", "e2b_api_key", "openai_api_key",
    "mistral_api_key", "minimax_api_key", "openrouter_api_key",
    "default_max_cost_usd", "log_level", "max_workflow_depth",
    "workflow_default_model",
}
# Keys in restorable settings that may be stored encrypted
_ENCRYPTED_RESTORABLE = {
    "anthropic_api_key", "e2b_api_key", "openai_api_key",
    "mistral_api_key", "minimax_api_key", "openrouter_api_key",
}


def _maybe_decrypt(key: str, value: str) -> str | None:
    """Decrypt a Fernet-encrypted value; None means skip this setting."""
    if isinstance(value, str) and value.startswith("gAAAAA"):
        try:
            from sandcastle.engine.crypto import decrypt_credentials

            decrypted = decrypt_credentials(value)
            if isinstance(decrypted, dict) and "v" in decrypted:
                return decrypted["v"]
        except Exception:
            logger.warning("Could not decrypt saved setting '%s', skipping", key)
            return None
    return value


async def restore_db_settings() -> int:
    """Apply DB-persisted settings to the live config; returns count applied."""
    from sqlalchemy import select as sa_select

    from sandcastle.config import Settings, settings
    from sandcastle.models.db import Setting, async_session

    async with async_session() as session:
        result = await session.execute(sa_select(Setting))
        saved = {s.key: s.value for s in result.scalars().all()}

    applied = 0
    for key, value in saved.items():
        if key not in _RESTORABLE_SETTINGS:
            if hasattr(settings, key):
                logger.debug("Skipping non-restorable saved setting '%s'", key)
            continue
        if not hasattr(settings, key):
            continue
        if key in _ENCRYPTED_RESTORABLE:
            value = _maybe_decrypt(key, value)
            if value is None:
                continue
        field_type = type(getattr(settings, key))
        try:
            if field_type is bool:
                coerced: object = value.lower() in ("true", "1", "yes")
            elif field_type is int:
                coerced = int(value)
            elif field_type is float:
                coerced = float(value)
            else:
                coerced = value
            # Validate through Pydantic to enforce field validators
            validated = Settings.model_validate({**settings.model_dump(), key: coerced})
            setattr(settings, key, getattr(validated, key))
            applied += 1
        except Exception as e:
            # Never log the value - it may be an API key or secret
            logger.warning(f"Ignoring invalid saved setting {key}=<redacted>: {e}")

    if saved:
        logger.info(f"Loaded {applied} saved settings from database")

    # Restore tool credentials (TOOL_* keys) into os.environ so connectors work
    tool_cred_count = 0
    for key, value in saved.items():
        if key.startswith("TOOL_") and value:
            value = _maybe_decrypt(key, value)
            if value is None:
                continue
            os.environ[key] = value
            tool_cred_count += 1
    if tool_cred_count:
        logger.info(f"Restored {tool_cred_count} tool credential(s) from database")

    return applied
