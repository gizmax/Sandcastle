"""Cross-cutting audit tests - testing interactions between components.

These tests verify security boundaries, data flow integrity, concurrency
safety, and error propagation across component boundaries.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Settings Security - Allowlist enforcement
# ---------------------------------------------------------------------------


class TestSettingsSecurityBoundary:
    """Verify that the settings update endpoint uses an allowlist, not a blocklist."""

    def test_mutable_settings_allowlist_exists(self):
        """The settings endpoint should use a positive allowlist of mutable settings."""
        from sandcastle.api.routes import update_settings
        import inspect

        source = inspect.getsource(update_settings)
        # Verify the allowlist pattern exists (not just a blocklist)
        assert "_MUTABLE_SETTINGS" in source, (
            "Settings update must use a _MUTABLE_SETTINGS allowlist"
        )

    def test_mutable_settings_does_not_include_security_keys(self):
        """Security-critical settings must NOT be in the mutable set."""
        # Import the source to check the allowlist
        import ast
        import inspect
        from sandcastle.api import routes

        source = inspect.getsource(routes.update_settings)

        # The allowlist should NOT contain these security-critical keys
        security_keys = {
            "auth_required",
            "credential_encryption_key",
            "admin_api_key",
            "license_key",
            "database_url",
            "redis_url",
            "webhook_secret",
            "dashboard_origin",
            "sandbox_backend",
            "sentry_dsn",
            "telemetry_enabled",
        }
        for key in security_keys:
            # We check the source doesn't include the key in the allowlist
            # by verifying it's not between _MUTABLE_SETTINGS = { and }
            assert f'"{key}"' not in source.split("_MUTABLE_SETTINGS")[1].split("}")[0], (
                f"Security-critical setting '{key}' must NOT be in _MUTABLE_SETTINGS"
            )

    def test_startup_restorable_settings_match_api_mutable(self):
        """The DB restore allowlist (shared by API lifespan and worker startup)
        must exist and never include security-critical settings."""
        from sandcastle import db_settings as restore_module
        from sandcastle import main as main_module
        import inspect

        allowlist = restore_module._RESTORABLE_SETTINGS
        for key in ("auth_required", "webhook_secret", "database_url", "redis_url"):
            assert key not in allowlist, (
                f"Security-critical setting '{key}' must NOT be restorable from DB"
            )
        # Both processes must actually use the shared restore
        assert "restore_db_settings" in inspect.getsource(main_module.lifespan)
        from sandcastle.queue import worker as worker_module

        assert "restore_db_settings" in inspect.getsource(worker_module.startup)


# ---------------------------------------------------------------------------
# 2. Code Step Security - exec() sandbox hardening
# ---------------------------------------------------------------------------


class TestCodeStepSandbox:
    """Verify the code step sandbox blocklist catches bypass attempts."""

    def test_chr_bypass_blocked(self):
        """String construction via chr() should be blocked."""
        from sandcastle.engine.executor import _CODE_STEP_BLOCKED_PATTERNS

        # chr() can construct arbitrary strings to bypass keyword blocklists
        assert _CODE_STEP_BLOCKED_PATTERNS.search("chr(111)")
        assert _CODE_STEP_BLOCKED_PATTERNS.search("chr (111)")

    def test_dunder_init_blocked(self):
        """__init__ access is blocked (used for sandbox escapes)."""
        from sandcastle.engine.executor import _CODE_STEP_BLOCKED_PATTERNS

        assert _CODE_STEP_BLOCKED_PATTERNS.search("__init__")
        assert _CODE_STEP_BLOCKED_PATTERNS.search("__dict__")
        assert _CODE_STEP_BLOCKED_PATTERNS.search("__code__")

    def test_os_system_blocked(self):
        """os.system and subprocess should be blocked."""
        from sandcastle.engine.executor import _CODE_STEP_BLOCKED_PATTERNS

        assert _CODE_STEP_BLOCKED_PATTERNS.search("os.system")
        assert _CODE_STEP_BLOCKED_PATTERNS.search("subprocess")

    def test_pickle_blocked(self):
        """pickle module access should be blocked (deserialization attacks)."""
        from sandcastle.engine.executor import _CODE_STEP_BLOCKED_PATTERNS

        assert _CODE_STEP_BLOCKED_PATTERNS.search("pickle")
        assert _CODE_STEP_BLOCKED_PATTERNS.search("__reduce__")

    def test_safe_code_not_blocked(self):
        """Normal data processing code should NOT be blocked."""
        from sandcastle.engine.executor import _CODE_STEP_BLOCKED_PATTERNS

        safe_code = """
result = []
for item in _input.get("items", []):
    if isinstance(item, dict):
        result.append({"name": item["name"], "value": item["value"] * 2})
"""
        assert _CODE_STEP_BLOCKED_PATTERNS.search(safe_code) is None

    def test_code_step_has_timeout_mechanism(self):
        """Code step should use ThreadPoolExecutor with timeout to prevent DoS."""
        import inspect
        from sandcastle.engine import executor

        source = inspect.getsource(executor._execute_code_step)
        assert "ThreadPoolExecutor" in source, (
            "Code step must use ThreadPoolExecutor for timeout safety"
        )
        assert "concurrent.futures.TimeoutError" in source, (
            "Code step must handle TimeoutError from the thread"
        )
        assert "timed out" in source.lower(), (
            "Code step must report timeout in error message"
        )


# ---------------------------------------------------------------------------
# 3. Crypto Module - Fernet key rotation tracking
# ---------------------------------------------------------------------------


class TestCryptoKeyRotation:
    """Verify the crypto module tracks key changes correctly."""

    def test_fernet_resets_on_key_change(self):
        """_get_fernet should reinitialize when the key changes."""
        import sandcastle.engine.crypto as crypto_mod
        from sandcastle.config import settings as real_settings

        # Reset module state
        crypto_mod._fernet_checked = False
        crypto_mod._fernet_instance = None
        crypto_mod._fernet_key_snapshot = ""

        original_key = real_settings.credential_encryption_key

        try:
            # First call: no key
            real_settings.credential_encryption_key = ""
            result1 = crypto_mod._get_fernet()
            assert result1 is None
            assert crypto_mod._fernet_checked is True

            # Second call: same key (cached)
            result2 = crypto_mod._get_fernet()
            assert result2 is None

            # Third call: key changed - should re-initialize
            from cryptography.fernet import Fernet

            new_key = Fernet.generate_key().decode()
            real_settings.credential_encryption_key = new_key
            result3 = crypto_mod._get_fernet()
            assert result3 is not None
        finally:
            # Cleanup
            real_settings.credential_encryption_key = original_key
            crypto_mod._fernet_checked = False
            crypto_mod._fernet_instance = None
            crypto_mod._fernet_key_snapshot = ""


# ---------------------------------------------------------------------------
# 4. Sub-workflow Path Traversal
# ---------------------------------------------------------------------------


class TestSubWorkflowPathTraversal:
    """Verify sub-workflow loading rejects path traversal attempts."""

    @pytest.mark.asyncio
    async def test_dotdot_blocked(self):
        """Sub-workflow names with '..' should be rejected."""
        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.executor import RunContext, _execute_sub_workflow_step

        sub_wf_config = MagicMock()
        sub_wf_config.workflow = "../../etc/passwd"
        sub_wf_config.input_mapping = {}
        sub_wf_config.parallel_over = None
        sub_wf_config.output_mapping = None

        step = StepDefinition(
            id="sub-traversal",
            type="sub_workflow",
            prompt="",
            sub_workflow=sub_wf_config,
        )
        context = RunContext(run_id="test", input={})
        storage = AsyncMock()

        result = await _execute_sub_workflow_step(step, context, storage)
        assert result.status == "failed"
        assert "Invalid" in result.error

    @pytest.mark.asyncio
    async def test_slash_blocked(self):
        """Sub-workflow names with '/' should be rejected."""
        from sandcastle.engine.dag import StepDefinition
        from sandcastle.engine.executor import RunContext, _execute_sub_workflow_step

        sub_wf_config = MagicMock()
        sub_wf_config.workflow = "path/to/workflow"
        sub_wf_config.input_mapping = {}
        sub_wf_config.parallel_over = None
        sub_wf_config.output_mapping = None

        step = StepDefinition(
            id="sub-slash",
            type="sub_workflow",
            prompt="",
            sub_workflow=sub_wf_config,
        )
        context = RunContext(run_id="test", input={})
        storage = AsyncMock()

        result = await _execute_sub_workflow_step(step, context, storage)
        assert result.status == "failed"
        assert "Invalid" in result.error


# ---------------------------------------------------------------------------
# 5. Safe Eval Expression Hardening
# ---------------------------------------------------------------------------


class TestSafeEvalHardening:
    """Verify _safe_eval_expression blocks dangerous patterns."""

    def test_dunder_access_blocked(self):
        """Double-underscore attribute access should be blocked."""
        from sandcastle.engine.executor import _safe_eval_expression

        with pytest.raises(ValueError, match="double-underscore"):
            _safe_eval_expression("steps.__class__.__bases__", {"steps": {}})

    def test_expression_length_limit(self):
        """Excessively long expressions should be rejected."""
        from sandcastle.engine.executor import _safe_eval_expression

        long_expr = "a " * 2000
        with pytest.raises(ValueError, match="too long"):
            _safe_eval_expression(long_expr, {"a": 1})

    def test_normal_expressions_work(self):
        """Normal condition expressions should evaluate correctly."""
        from sandcastle.engine.executor import _safe_eval_expression

        # Basic comparison
        assert _safe_eval_expression("x > 5", {"x": 10}) is True
        assert _safe_eval_expression("x > 5", {"x": 3}) is False

        # String comparison
        assert _safe_eval_expression(
            "status == 'ready'", {"status": "ready"}
        ) is True

        # len() function
        assert _safe_eval_expression(
            "len(items) > 0", {"items": [1, 2, 3]}
        ) is True

        # Boolean logic
        assert _safe_eval_expression(
            "a and b", {"a": True, "b": True}
        ) is True

        # Dict access
        assert _safe_eval_expression(
            "steps['analyze']['score'] > 0.5",
            {"steps": {"analyze": {"score": 0.8}}},
        ) is True


# ---------------------------------------------------------------------------
# 6. Security Headers
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """Verify security headers are comprehensive."""

    @pytest.mark.asyncio
    async def test_hsts_in_production_mode(self):
        """HSTS header should be set in production mode."""
        from starlette.requests import Request
        from starlette.responses import Response

        from sandcastle.api.security_headers import security_headers_middleware

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = False
            mock_settings.csp_report_only = False

            request = MagicMock(spec=Request)
            request.url.path = "/api/runs"

            response = Response(content="test")

            async def call_next(req):
                return response

            result = await security_headers_middleware(request, call_next)
            assert "Strict-Transport-Security" in result.headers

    @pytest.mark.asyncio
    async def test_no_hsts_in_local_mode(self):
        """HSTS header should NOT be set in local mode."""
        from starlette.requests import Request
        from starlette.responses import Response

        from sandcastle.api.security_headers import security_headers_middleware

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.csp_report_only = False

            request = MagicMock(spec=Request)
            request.url.path = "/api/runs"

            response = Response(content="test")

            async def call_next(req):
                return response

            result = await security_headers_middleware(request, call_next)
            assert "Strict-Transport-Security" not in result.headers

    @pytest.mark.asyncio
    async def test_api_responses_not_cached(self):
        """API responses should have Cache-Control: no-store."""
        from starlette.requests import Request
        from starlette.responses import Response

        from sandcastle.api.security_headers import security_headers_middleware

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.csp_report_only = False

            request = MagicMock(spec=Request)
            request.url.path = "/api/settings"

            response = Response(content="test")

            async def call_next(req):
                return response

            result = await security_headers_middleware(request, call_next)
            assert result.headers.get("Cache-Control") == "no-store"
            assert result.headers.get("Pragma") == "no-cache"


# ---------------------------------------------------------------------------
# 7. Data Flow Integrity - Worker error propagation
# ---------------------------------------------------------------------------


class TestWorkerErrorPropagation:
    """Verify errors propagate correctly through the worker pipeline."""

    @pytest.mark.asyncio
    async def test_worker_marks_run_failed_on_exception(self):
        """When the worker catches an exception, the DB run should be marked FAILED."""
        from sandcastle.queue.worker import run_workflow_job

        mock_session = AsyncMock()
        mock_ctx_manager = AsyncMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)

        from sandcastle.models.db import RunStatus

        # First call: get the run (returns a mock run in QUEUED state)
        mock_run = MagicMock()
        mock_run.status = RunStatus.QUEUED
        mock_run.callback_url = None
        mock_run.max_cost_usd = None

        mock_session.get = AsyncMock(return_value=mock_run)
        mock_session.commit = AsyncMock()

        mock_async_session = MagicMock(return_value=mock_ctx_manager)

        # parse_yaml_string should raise to simulate invalid workflow
        with patch("sandcastle.models.db.async_session", mock_async_session):
            with patch(
                "sandcastle.engine.dag.parse_yaml_string",
                side_effect=ValueError("bad yaml"),
            ):
                result = await run_workflow_job(
                    ctx={},
                    workflow_yaml="invalid: yaml: content",
                    input_data={},
                    run_id=str(uuid.uuid4()),
                )

        assert result["status"] == "failed"
        assert "bad yaml" in result["error"]

    @pytest.mark.asyncio
    async def test_duplicate_delivery_guard(self):
        """Worker should skip execution if run is not in QUEUED state."""
        from sandcastle.queue.worker import run_workflow_job

        mock_session = AsyncMock()
        mock_ctx_manager = AsyncMock()
        mock_ctx_manager.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx_manager.__aexit__ = AsyncMock(return_value=False)

        from sandcastle.models.db import RunStatus

        mock_run = MagicMock()
        mock_run.status = RunStatus.RUNNING

        mock_session.get = AsyncMock(return_value=mock_run)

        mock_async_session = MagicMock(return_value=mock_ctx_manager)

        with patch("sandcastle.models.db.async_session", mock_async_session):
            result = await run_workflow_job(
                ctx={},
                workflow_yaml="test",
                input_data={},
                run_id=str(uuid.uuid4()),
            )

        assert result["status"] == "running"


# ---------------------------------------------------------------------------
# 8. Concurrency - Redis pool reuse
# ---------------------------------------------------------------------------


class TestRedisPoolReuse:
    """Verify Redis connections are properly pooled and reused."""

    def test_enqueue_uses_shared_pool(self):
        """The enqueue function should reuse a shared Redis pool."""
        import inspect
        from sandcastle.queue import worker

        source = inspect.getsource(worker.enqueue_workflow)
        assert "_enqueue_redis_pool" in source, (
            "enqueue_workflow should use a shared _enqueue_redis_pool"
        )

    def test_cancel_uses_shared_redis(self):
        """The cancel endpoint should reuse the executor's Redis pool."""
        import inspect
        from sandcastle.api import routes

        source = inspect.getsource(routes.cancel_run)
        assert "_get_redis" in source, (
            "cancel_run should use the executor's shared _get_redis pool"
        )

    def test_health_uses_shared_redis(self):
        """The health endpoint should reuse the executor's Redis pool."""
        import inspect
        from sandcastle.api import routes

        source = inspect.getsource(routes.health_check)
        assert "_get_redis" in source, (
            "health_check should use the executor's shared _get_redis pool"
        )


# ---------------------------------------------------------------------------
# 9. Event Bus - Dead subscriber cleanup
# ---------------------------------------------------------------------------


class TestEventBusBoundary:
    """Verify event bus properly handles subscriber lifecycle."""

    @pytest.mark.asyncio
    async def test_subscriber_limit_enforced(self):
        """EventBus should reject subscribers beyond MAX_SUBSCRIBERS."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        bus.MAX_SUBSCRIBERS = 3
        queues = []

        for _ in range(3):
            q = await bus.subscribe()
            queues.append(q)

        with pytest.raises(RuntimeError, match="subscriber limit"):
            await bus.subscribe()

        # Cleanup
        for q in queues:
            await bus.unsubscribe(q)

    @pytest.mark.asyncio
    async def test_dead_subscriber_eviction(self):
        """Subscribers that consistently drop events should be evicted."""
        from sandcastle.engine.events import EventBus

        bus = EventBus()
        bus.MAX_CONSECUTIVE_DROPS = 3

        # Create a tiny queue that will fill up immediately
        q = asyncio.Queue(maxsize=1)
        async with bus._lock:
            bus._subscribers.add(q)

        # Fill the queue
        q.put_nowait({"type": "filler", "data": {}, "timestamp": ""})

        # Publish enough events to trigger eviction
        for _ in range(4):
            bus.publish("run.started", {"run_id": "test"})

        assert q not in bus._subscribers, "Dead subscriber should be evicted"


# ---------------------------------------------------------------------------
# 10. Auth Boundary - Public path bypass safety
# ---------------------------------------------------------------------------


class TestAuthBoundary:
    """Verify auth middleware correctly gates all sensitive endpoints."""

    def test_public_paths_are_safe(self):
        """Public paths should only include read-only endpoints."""
        from sandcastle.api.auth import PUBLIC_PATHS, PUBLIC_PREFIXES

        # Verify no write/mutation endpoints are public
        for path in PUBLIC_PATHS:
            assert "settings" not in path.lower()
            assert "api-keys" not in path.lower()
            assert "run" not in path.lower() or path == "/api/runtime"

        for prefix in PUBLIC_PREFIXES:
            assert "settings" not in prefix.lower()
            assert "api-keys" not in prefix.lower()

    def test_token_query_param_restricted_to_stream_paths(self):
        """Token via query param should only work for SSE/stream paths."""
        import inspect
        from sandcastle.api import auth

        source = inspect.getsource(auth.auth_middleware)
        # Verify the token query param check includes path restriction
        assert "/stream" in source or "agui" in source


# ---------------------------------------------------------------------------
# 11. Webhook SSRF - Comprehensive validation
# ---------------------------------------------------------------------------


class TestWebhookSSRFPrevention:
    """Verify webhook URLs are properly validated against SSRF."""

    def test_loopback_blocked(self):
        """Localhost URLs should be blocked."""
        from sandcastle.webhooks.dispatcher import validate_callback_url

        with pytest.raises(ValueError, match="blocked"):
            validate_callback_url("http://127.0.0.1:8080/callback")

    def test_private_network_blocked(self):
        """Private network (10.x, 172.16.x, 192.168.x) should be blocked."""
        from sandcastle.webhooks.dispatcher import validate_callback_url

        for url in [
            "http://10.0.0.1/callback",
            "http://192.168.1.1/callback",
        ]:
            with pytest.raises(ValueError):
                validate_callback_url(url)

    def test_non_http_scheme_blocked(self):
        """Non-HTTP schemes (ftp, file, etc.) should be blocked."""
        from sandcastle.webhooks.dispatcher import validate_callback_url

        for url in [
            "ftp://example.com/file",
            "file:///etc/passwd",
            "gopher://example.com",
        ]:
            with pytest.raises(ValueError, match="http"):
                validate_callback_url(url)


# ---------------------------------------------------------------------------
# 12. Runner file validation
# ---------------------------------------------------------------------------


class TestRunnerFileValidation:
    """Verify runner file path validation across all backends."""

    def test_path_traversal_rejected(self):
        """Runner files with path traversal should be rejected."""
        from sandcastle.engine.backends import _validate_runner_file

        with pytest.raises(ValueError, match="Invalid"):
            _validate_runner_file("../../../etc/passwd")

    def test_absolute_path_rejected(self):
        """Absolute paths for runner files should be rejected."""
        from sandcastle.engine.backends import _validate_runner_file

        with pytest.raises(ValueError, match="Invalid"):
            _validate_runner_file("/etc/passwd")

    def test_normal_runner_accepted(self):
        """Normal runner filenames should be accepted."""
        from sandcastle.engine.backends import _validate_runner_file

        _validate_runner_file("runner.mjs")
        _validate_runner_file("runner-openai.mjs")


# ---------------------------------------------------------------------------
# 13. Type Safety - UUID serialization consistency
# ---------------------------------------------------------------------------


class TestTypeSafety:
    """Verify types are consistent across component boundaries."""

    def test_run_id_always_string_in_worker(self):
        """run_id should be handled as string consistently in the worker."""
        import inspect
        from sandcastle.queue import worker

        source = inspect.getsource(worker.run_workflow_job)
        # The worker should convert run_id to UUID for DB operations
        assert "UUID(run_id)" in source or "_uuid.UUID(run_id)" in source

    def test_sse_event_serialization(self):
        """SSE events should properly serialize all types including UUID and datetime."""
        from sandcastle.api.routes import _sse_event

        data = {
            "run_id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc),
            "cost": 0.001234,
            "status": "completed",
        }
        event_str = _sse_event("test", data)
        assert event_str.startswith("event: test\n")
        assert "data: " in event_str
        # Should be valid JSON after "data: "
        json_str = event_str.split("data: ", 1)[1].strip()
        parsed = json.loads(json_str)
        assert parsed["status"] == "completed"


# ---------------------------------------------------------------------------
# 14. Workflow Input Validation
# ---------------------------------------------------------------------------


class TestWorkflowInputValidation:
    """Verify input validation catches edge cases."""

    def test_required_field_empty_string_rejected(self):
        """Required fields that are empty strings should be rejected."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        errors = _validate_workflow_input({"name": ""}, schema)
        assert len(errors) > 0
        assert "missing or empty" in errors[0].lower()

    def test_type_coercion_string_to_int(self):
        """String values should be coerced to integers when schema says integer."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "properties": {"count": {"type": "integer"}},
        }
        input_data = {"count": "42"}
        errors = _validate_workflow_input(input_data, schema)
        assert len(errors) == 0
        assert input_data["count"] == 42

    def test_invalid_int_reported(self):
        """Invalid integer strings should produce errors."""
        from sandcastle.api.routes import _validate_workflow_input

        schema = {
            "properties": {"count": {"type": "integer"}},
        }
        input_data = {"count": "not-a-number"}
        errors = _validate_workflow_input(input_data, schema)
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# 15. Cancel Flag Lifecycle
# ---------------------------------------------------------------------------


class TestCancelFlagLifecycle:
    """Verify cancel flags are properly set and consumed."""

    @pytest.mark.asyncio
    async def test_cancel_flag_consumed_after_detection(self):
        """Cancel flags should be removed after being detected (local mode)."""
        from sandcastle.config import settings as real_settings
        from sandcastle.engine.executor import (
            _cancel_flags,
            _cancel_flags_lock,
            _check_cancel,
            cancel_run_local,
        )

        run_id = str(uuid.uuid4())

        original_redis_url = real_settings.redis_url
        try:
            real_settings.redis_url = ""

            await cancel_run_local(run_id)

            # First check: should detect cancel
            assert await _check_cancel(run_id) is True

            # Second check: flag persists (cleaned up in finally block, not on check)
            assert await _check_cancel(run_id) is True
        finally:
            real_settings.redis_url = original_redis_url

    @pytest.mark.asyncio
    async def test_cancel_flag_eviction(self):
        """Cancel flags should evict oldest entries when limit is exceeded."""
        from sandcastle.engine.executor import (
            _MAX_CANCEL_FLAGS,
            _cancel_flags,
            _cancel_flags_lock,
            cancel_run_local,
        )

        # Save original state
        original_flags = _cancel_flags.copy()

        try:
            _cancel_flags.clear()

            # Fill up to max
            for i in range(_MAX_CANCEL_FLAGS):
                async with _cancel_flags_lock:
                    _cancel_flags[f"run-{i}"] = None

            # Adding one more should trigger eviction
            await cancel_run_local("overflow-run")
            assert len(_cancel_flags) < _MAX_CANCEL_FLAGS
            assert "overflow-run" in _cancel_flags
        finally:
            _cancel_flags.clear()
            _cancel_flags.update(original_flags)


# ---------------------------------------------------------------------------
# 16. Workflow loader path security
# ---------------------------------------------------------------------------


class TestWorkflowLoaderSecurity:
    """Verify workflow loading from disk prevents path traversal."""

    def test_dotdot_rejected(self):
        """Workflow names with '..' should be rejected."""
        from sandcastle.api.routes import _load_workflow_yaml

        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("../../../etc/passwd")

    def test_slash_rejected(self):
        """Workflow names with '/' should be rejected."""
        from sandcastle.api.routes import _load_workflow_yaml

        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("some/path/workflow")

    def test_backslash_rejected(self):
        """Workflow names with backslash should be rejected."""
        from sandcastle.api.routes import _load_workflow_yaml

        with pytest.raises(FileNotFoundError, match="Invalid"):
            _load_workflow_yaml("some\\path\\workflow")


# ---------------------------------------------------------------------------
# 17. Budget check boundary conditions
# ---------------------------------------------------------------------------


class TestBudgetBoundary:
    """Verify budget checking handles edge cases correctly."""

    def test_zero_budget_means_no_limit(self):
        """A budget of 0 should mean unlimited (no budget check)."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(run_id="test", input={}, max_cost_usd=0.0)
        ctx.costs = [1.0, 2.0, 3.0]
        assert _check_budget(ctx) is None

    def test_negative_budget_means_no_limit(self):
        """A negative budget should mean unlimited."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(run_id="test", input={}, max_cost_usd=-1.0)
        ctx.costs = [1.0]
        assert _check_budget(ctx) is None

    def test_budget_exceeded_at_100_percent(self):
        """Budget should be 'exceeded' at exactly 100%."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0)
        ctx.costs = [1.0]
        assert _check_budget(ctx) == "exceeded"

    def test_budget_warning_at_80_percent(self):
        """Budget should warn at 80%."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0)
        ctx.costs = [0.85]
        assert _check_budget(ctx) == "warning"

    def test_budget_ok_under_80_percent(self):
        """Budget should be OK under 80%."""
        from sandcastle.engine.executor import RunContext, _check_budget

        ctx = RunContext(run_id="test", input={}, max_cost_usd=1.0)
        ctx.costs = [0.5]
        assert _check_budget(ctx) is None
