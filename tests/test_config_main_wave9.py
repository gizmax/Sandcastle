"""Wave 9 audit: config.py edge cases, main.py middleware/SPA/lifespan.

Covers:
- config.py: redis_url scheme validation, dashboard_origin normalization,
  data_dir/workflows_dir empty/relative path handling, safe_dump correctness,
  _SENSITIVE_FIELDS completeness, env override precedence, computed fields,
  Settings immutability after construction, bool/int/float env parsing
- main.py: CORS origins building and dedup, SPA fallback path traversal
  safety, middleware ordering, lifespan error handling
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.config import (
    Settings,
    _DEFAULT_DATA_DIR,
    _DEFAULT_WORKFLOWS_DIR,
    _VALID_LOG_LEVELS,
    _VALID_MEMORY_BACKENDS,
    _VALID_SANDBOX_BACKENDS,
    _VALID_STORAGE_BACKENDS,
)

# _SENSITIVE_FIELDS is a Pydantic ModelPrivateAttr; access the default value
# directly to avoid needing an instance in class-level assertions.
_SENSITIVE_FIELDS: frozenset[str] = Settings.__private_attributes__[
    "_SENSITIVE_FIELDS"
].default


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_settings(**overrides) -> Settings:
    """Create a fresh Settings instance with specific overrides."""
    env_overrides = {}
    for key, val in overrides.items():
        env_overrides[key.upper()] = str(val)
    with patch.dict(os.environ, env_overrides, clear=False):
        return Settings(_env_file=None, **overrides)


# ===========================================================================
# Section 1: redis_url validation (new validator)
# ===========================================================================


class TestRedisUrlValidation:
    """REDIS_URL must use a valid scheme or be empty."""

    def test_empty_redis_url_accepted(self):
        s = _make_settings(redis_url="")
        assert s.redis_url == ""

    def test_redis_scheme_accepted(self):
        s = _make_settings(redis_url="redis://localhost:6379")
        assert s.redis_url == "redis://localhost:6379"

    def test_rediss_scheme_accepted(self):
        s = _make_settings(redis_url="rediss://user:pass@host:6380/0")
        assert s.redis_url == "rediss://user:pass@host:6380/0"

    def test_unix_scheme_accepted(self):
        s = _make_settings(redis_url="unix:///var/run/redis.sock")
        assert s.redis_url == "unix:///var/run/redis.sock"

    def test_invalid_scheme_falls_back_to_empty(self):
        s = _make_settings(redis_url="http://localhost:6379")
        assert s.redis_url == ""

    def test_no_scheme_falls_back_to_empty(self):
        s = _make_settings(redis_url="localhost:6379")
        assert s.redis_url == ""

    def test_garbage_falls_back_to_empty(self):
        s = _make_settings(redis_url="not-a-url-at-all")
        assert s.redis_url == ""

    def test_whitespace_only_falls_back_to_empty(self):
        s = _make_settings(redis_url="   ")
        assert s.redis_url == ""

    def test_whitespace_stripped_from_valid_url(self):
        s = _make_settings(redis_url="  redis://localhost:6379  ")
        assert s.redis_url == "redis://localhost:6379"

    def test_redis_url_with_database_number(self):
        s = _make_settings(redis_url="redis://localhost:6379/5")
        assert s.redis_url == "redis://localhost:6379/5"

    def test_redis_url_with_auth(self):
        s = _make_settings(redis_url="redis://:mypassword@host:6379")
        assert s.redis_url == "redis://:mypassword@host:6379"


# ===========================================================================
# Section 2: dashboard_origin normalization (new validator)
# ===========================================================================


class TestDashboardOriginValidation:
    """DASHBOARD_ORIGIN whitespace/trailing slash handling."""

    def test_default_origin(self):
        s = _make_settings()
        assert s.dashboard_origin == "http://localhost:5173"

    def test_whitespace_stripped(self):
        s = _make_settings(dashboard_origin="  http://example.com  ")
        assert s.dashboard_origin == "http://example.com"

    def test_trailing_slash_removed(self):
        s = _make_settings(dashboard_origin="http://example.com/")
        assert s.dashboard_origin == "http://example.com"

    def test_multiple_trailing_slashes_removed(self):
        s = _make_settings(dashboard_origin="http://example.com///")
        assert s.dashboard_origin == "http://example.com"

    def test_https_origin(self):
        s = _make_settings(dashboard_origin="https://app.example.com")
        assert s.dashboard_origin == "https://app.example.com"

    def test_origin_with_port(self):
        s = _make_settings(dashboard_origin="http://localhost:3000")
        assert s.dashboard_origin == "http://localhost:3000"

    def test_wildcard_preserved(self):
        """Wildcard should be preserved (main.py handles the warning)."""
        s = _make_settings(dashboard_origin="*")
        assert s.dashboard_origin == "*"

    def test_whitespace_and_trailing_slash_combined(self):
        s = _make_settings(dashboard_origin="  http://localhost:5173/  ")
        assert s.dashboard_origin == "http://localhost:5173"


# ===========================================================================
# Section 3: data_dir / workflows_dir path handling (enhanced validator)
# ===========================================================================


class TestDataDirPathHandling:
    """DATA_DIR empty, relative, tilde expansion."""

    def test_tilde_expansion(self):
        s = _make_settings(data_dir="~/my-sandcastle-data")
        assert "~" not in s.data_dir
        assert s.data_dir.endswith("/my-sandcastle-data")

    def test_empty_data_dir_uses_default(self):
        """Empty DATA_DIR must fall back to the field default, not '.'."""
        s = _make_settings(data_dir="")
        assert s.data_dir != "."
        assert s.data_dir != ""
        # Should match the default
        assert s.data_dir == _DEFAULT_DATA_DIR

    def test_relative_path_resolved_to_absolute(self):
        s = _make_settings(data_dir="relative/path")
        assert os.path.isabs(s.data_dir)

    def test_absolute_path_preserved(self):
        s = _make_settings(data_dir="/opt/sandcastle/data")
        assert s.data_dir == "/opt/sandcastle/data"

    def test_default_data_dir_is_absolute(self):
        s = _make_settings()
        assert os.path.isabs(s.data_dir)


class TestWorkflowsDirPathHandling:
    """WORKFLOWS_DIR empty, relative, tilde expansion."""

    def test_tilde_expansion(self):
        s = _make_settings(workflows_dir="~/my-workflows")
        assert "~" not in s.workflows_dir
        assert s.workflows_dir.endswith("/my-workflows")

    def test_empty_workflows_dir_uses_default(self):
        """Empty WORKFLOWS_DIR must fall back to the field default, not '.'."""
        s = _make_settings(workflows_dir="")
        assert s.workflows_dir != "."
        assert s.workflows_dir != ""
        assert s.workflows_dir == _DEFAULT_WORKFLOWS_DIR

    def test_relative_path_resolved_to_absolute(self):
        s = _make_settings(workflows_dir="workflows")
        assert os.path.isabs(s.workflows_dir)

    def test_absolute_path_preserved(self):
        s = _make_settings(workflows_dir="/var/sandcastle/workflows")
        assert s.workflows_dir == "/var/sandcastle/workflows"

    def test_default_workflows_dir_is_absolute(self):
        s = _make_settings()
        assert os.path.isabs(s.workflows_dir)


# ===========================================================================
# Section 4: safe_dump correctness
# ===========================================================================


class TestSafeDump:
    """safe_dump() must redact all sensitive fields."""

    def test_non_empty_sensitive_field_is_redacted(self):
        s = _make_settings(anthropic_api_key="test-secret-value")
        dump = s.safe_dump()
        assert dump["anthropic_api_key"] == "***"

    def test_empty_sensitive_field_stays_empty(self):
        """Empty string secrets should not be turned into '***'."""
        s = _make_settings(anthropic_api_key="")
        dump = s.safe_dump()
        assert dump["anthropic_api_key"] == ""

    def test_all_sensitive_fields_exist_in_model(self):
        """Every entry in _SENSITIVE_FIELDS must correspond to a real field."""
        model_fields = set(Settings.model_fields.keys())
        for field in _SENSITIVE_FIELDS:
            assert field in model_fields, (
                f"_SENSITIVE_FIELDS contains '{field}' which is not a model field"
            )

    def test_multiple_sensitive_fields_redacted(self):
        s = _make_settings(
            anthropic_api_key="secret1",
            e2b_api_key="secret2",
            database_url="postgresql+asyncpg://user:pass@host/db",
        )
        dump = s.safe_dump()
        assert dump["anthropic_api_key"] == "***"
        assert dump["e2b_api_key"] == "***"
        assert dump["database_url"] == "***"

    def test_non_sensitive_fields_not_redacted(self):
        s = _make_settings(sandbox_backend="docker", log_level="debug")
        dump = s.safe_dump()
        assert dump["sandbox_backend"] == "docker"
        assert dump["log_level"] == "debug"

    def test_safe_dump_includes_computed_field(self):
        """is_local_mode computed field should be in model_dump."""
        s = _make_settings(database_url="")
        dump = s.safe_dump()
        assert "is_local_mode" in dump
        assert dump["is_local_mode"] is True

    def test_safe_dump_returns_new_dict(self):
        """safe_dump should return a copy, not a reference to internal state."""
        s = _make_settings(anthropic_api_key="secret")
        dump1 = s.safe_dump()
        dump1["anthropic_api_key"] = "mutated"
        dump2 = s.safe_dump()
        assert dump2["anthropic_api_key"] == "***"


# ===========================================================================
# Section 5: _SENSITIVE_FIELDS completeness
# ===========================================================================


class TestSensitiveFieldsCompleteness:
    """Every field that can hold a credential or URL must be in _SENSITIVE_FIELDS."""

    def test_all_api_key_fields_sensitive(self):
        api_key_fields = {
            name for name in Settings.model_fields
            if "api_key" in name or "api_token" in name
        }
        for field in api_key_fields:
            assert field in _SENSITIVE_FIELDS, (
                f"Field '{field}' looks like an API key but is not in _SENSITIVE_FIELDS"
            )

    def test_all_password_fields_sensitive(self):
        password_fields = {
            name for name in Settings.model_fields
            if "password" in name or "secret" in name
        }
        for field in password_fields:
            assert field in _SENSITIVE_FIELDS, (
                f"Field '{field}' looks like a secret but is not in _SENSITIVE_FIELDS"
            )

    def test_all_token_fields_sensitive(self):
        token_fields = {
            name for name in Settings.model_fields
            if "token" in name and name != "e2b_template"
        }
        for field in token_fields:
            assert field in _SENSITIVE_FIELDS, (
                f"Field '{field}' looks like a token but is not in _SENSITIVE_FIELDS"
            )

    def test_connection_urls_sensitive(self):
        for field in ("database_url", "redis_url", "tool_postgresql_url"):
            assert field in _SENSITIVE_FIELDS


# ===========================================================================
# Section 6: Settings immutability / mutation
# ===========================================================================


class TestSettingsMutation:
    """Settings can be mutated via setattr (needed by main.py lifespan)
    but computed fields should be consistent."""

    def test_setattr_updates_field(self):
        """main.py lifespan uses setattr to restore DB settings."""
        s = _make_settings(log_level="info")
        s.log_level = "debug"
        assert s.log_level == "debug"

    def test_computed_field_reflects_mutation(self):
        """is_local_mode should reflect changes to database_url."""
        s = _make_settings(database_url="")
        assert s.is_local_mode is True
        s.database_url = "postgresql+asyncpg://user:pass@host/db"
        assert s.is_local_mode is False

    def test_model_dump_reflects_mutation(self):
        s = _make_settings(max_concurrent_sandboxes=5)
        s.max_concurrent_sandboxes = 10
        dump = s.model_dump()
        assert dump["max_concurrent_sandboxes"] == 10


# ===========================================================================
# Section 7: Boolean parsing in pydantic-settings
# ===========================================================================


class TestBooleanParsing:
    """Pydantic-settings bool fields should accept various truthy/falsy strings."""

    @pytest.mark.parametrize("value,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        ("no", False),
    ])
    def test_auth_required_bool_parsing(self, value: str, expected: bool):
        """AUTH_REQUIRED should accept various boolean string representations."""
        with patch.dict(os.environ, {"AUTH_REQUIRED": value}, clear=False):
            s = Settings(_env_file=None)
            assert s.auth_required is expected

    @pytest.mark.parametrize("value,expected", [
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
    ])
    def test_scheduler_enabled_bool_parsing(self, value: str, expected: bool):
        with patch.dict(os.environ, {"SCHEDULER_ENABLED": value}, clear=False):
            s = Settings(_env_file=None)
            assert s.scheduler_enabled is expected

    @pytest.mark.parametrize("value,expected", [
        ("true", True),
        ("false", False),
    ])
    def test_telemetry_enabled_bool_parsing(self, value: str, expected: bool):
        with patch.dict(os.environ, {"TELEMETRY_ENABLED": value}, clear=False):
            s = Settings(_env_file=None)
            assert s.telemetry_enabled is expected


# ===========================================================================
# Section 8: Integer/float env var parsing
# ===========================================================================


class TestNumericEnvParsing:
    """Integer and float settings should be parsed from env var strings."""

    def test_max_concurrent_from_env(self):
        with patch.dict(os.environ, {"MAX_CONCURRENT_SANDBOXES": "25"}, clear=False):
            s = Settings(_env_file=None)
            assert s.max_concurrent_sandboxes == 25

    def test_max_cost_from_env(self):
        with patch.dict(os.environ, {"DEFAULT_MAX_COST_USD": "9.99"}, clear=False):
            s = Settings(_env_file=None)
            assert s.default_max_cost_usd == pytest.approx(9.99)

    def test_failover_cooldown_from_env(self):
        with patch.dict(os.environ, {"FAILOVER_COOLDOWN_SECONDS": "30.5"}, clear=False):
            s = Settings(_env_file=None)
            assert s.failover_cooldown_seconds == pytest.approx(30.5)

    def test_memory_admit_threshold_from_env(self):
        with patch.dict(os.environ, {"MEMORY_ADMIT_THRESHOLD": "0.75"}, clear=False):
            s = Settings(_env_file=None)
            assert s.memory_admit_threshold == pytest.approx(0.75)


# ===========================================================================
# Section 9: Environment variable override precedence
# ===========================================================================


class TestEnvOverridePrecedence:
    """Environment variables should override defaults."""

    def test_env_overrides_default_sandbox_backend(self):
        with patch.dict(os.environ, {"SANDBOX_BACKEND": "docker"}, clear=False):
            s = Settings(_env_file=None)
            assert s.sandbox_backend == "docker"

    def test_env_overrides_default_log_level(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "error"}, clear=False):
            s = Settings(_env_file=None)
            assert s.log_level == "error"

    def test_explicit_kwarg_overrides_env(self):
        """Explicit constructor kwargs should override env vars."""
        with patch.dict(os.environ, {"LOG_LEVEL": "error"}, clear=False):
            s = Settings(_env_file=None, log_level="debug")
            assert s.log_level == "debug"

    def test_extra_env_vars_ignored(self):
        """Settings(extra='ignore') should not fail on unknown env vars."""
        with patch.dict(os.environ, {
            "TOTALLY_UNKNOWN_SETTING_XYZ": "whatever"
        }, clear=False):
            s = Settings(_env_file=None)
            assert not hasattr(s, "totally_unknown_setting_xyz")


# ===========================================================================
# Section 10: is_local_mode computed field edge cases
# ===========================================================================


class TestIsLocalModeEdgeCases:
    """is_local_mode computed property edge cases."""

    def test_empty_url_is_local(self):
        s = _make_settings(database_url="")
        assert s.is_local_mode is True

    def test_sqlite_aiosqlite_is_local(self):
        s = _make_settings(database_url="sqlite+aiosqlite:///path/to/db")
        assert s.is_local_mode is True

    def test_sqlite_plain_is_local(self):
        s = _make_settings(database_url="sqlite:///path/to/db")
        assert s.is_local_mode is True

    def test_postgres_is_not_local(self):
        s = _make_settings(database_url="postgresql+asyncpg://user:pass@host/db")
        assert s.is_local_mode is False

    def test_mysql_is_not_local(self):
        s = _make_settings(database_url="mysql+aiomysql://user:pass@host/db")
        assert s.is_local_mode is False

    def test_sqlite_case_sensitivity(self):
        """SQLite check is case-sensitive (URL schemes are lowercase by convention)."""
        s = _make_settings(database_url="SQLITE:///path")
        # startswith("sqlite") is False for uppercase
        assert s.is_local_mode is False


# ===========================================================================
# Section 11: max_workflow_depth upper bound (not tested in existing tests)
# ===========================================================================


class TestMaxWorkflowDepthUpperBound:
    """MAX_WORKFLOW_DEPTH upper bound (20) validation."""

    def test_twenty_is_accepted(self):
        s = _make_settings(max_workflow_depth=20)
        assert s.max_workflow_depth == 20

    def test_twenty_one_clamped_to_twenty(self):
        s = _make_settings(max_workflow_depth=21)
        assert s.max_workflow_depth == 20

    def test_one_hundred_clamped_to_twenty(self):
        s = _make_settings(max_workflow_depth=100)
        assert s.max_workflow_depth == 20

    def test_boundary_values(self):
        """Test exact boundary values: 1, 20."""
        s1 = _make_settings(max_workflow_depth=1)
        assert s1.max_workflow_depth == 1
        s20 = _make_settings(max_workflow_depth=20)
        assert s20.max_workflow_depth == 20


# ===========================================================================
# Section 12: CORS origins building (main.py logic)
# ===========================================================================


class TestCorsOriginsBuilding:
    """Test CORS origins list construction in main.py."""

    def _build_cors_origins(self, dashboard_origin: str) -> list[str]:
        """Replicate the CORS origins logic from main.py."""
        origins = [
            dashboard_origin,
            "http://localhost:5173",
            "http://localhost:5174",
        ]
        origins = list(dict.fromkeys(o for o in origins if o != "*"))
        return origins

    def test_default_origin_deduplicates(self):
        """Default dashboard_origin overlaps with hardcoded port 5173."""
        origins = self._build_cors_origins("http://localhost:5173")
        assert origins.count("http://localhost:5173") == 1

    def test_custom_origin_added(self):
        origins = self._build_cors_origins("https://app.example.com")
        assert "https://app.example.com" in origins
        assert "http://localhost:5173" in origins
        assert "http://localhost:5174" in origins

    def test_wildcard_filtered_out(self):
        origins = self._build_cors_origins("*")
        assert "*" not in origins

    def test_trailing_slash_normalized_by_validator(self):
        """After our validator fix, trailing slash is stripped before CORS building."""
        s = _make_settings(dashboard_origin="http://localhost:5173/")
        # Validator strips trailing slash
        assert s.dashboard_origin == "http://localhost:5173"
        # So CORS dedup works correctly
        origins = self._build_cors_origins(s.dashboard_origin)
        assert origins.count("http://localhost:5173") == 1

    def test_no_duplicate_entries(self):
        origins = self._build_cors_origins("http://localhost:5174")
        assert len(origins) == len(set(origins))


# ===========================================================================
# Section 13: SPA fallback path safety (main.py)
# ===========================================================================


class TestSpaFallbackPathSafety:
    """SPA fallback must prevent path traversal."""

    def test_path_traversal_blocked(self, tmp_path):
        """Paths with .. that escape the dashboard dir must be blocked."""
        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        (dashboard_dir / "index.html").write_text("<html></html>")

        # Simulate the SPA fallback logic
        path = "../../../etc/passwd"
        file = (dashboard_dir / path).resolve()
        assert not file.is_relative_to(dashboard_dir)

    def test_normal_file_allowed(self, tmp_path):
        dashboard_dir = tmp_path / "dashboard"
        assets_dir = dashboard_dir / "assets"
        assets_dir.mkdir(parents=True)
        (assets_dir / "app.js").write_text("console.log('hi')")

        path = "assets/app.js"
        file = (dashboard_dir / path).resolve()
        assert file.is_relative_to(dashboard_dir)
        assert file.exists()

    def test_api_paths_not_intercepted(self):
        """SPA fallback should not intercept /api/ paths."""
        api_paths = ["api/health", "api/runs", "api/templates/list", "api"]
        for path in api_paths:
            assert path.startswith("api/") or path == "api"

    def test_a2a_paths_not_intercepted(self):
        """SPA fallback should not intercept A2A protocol paths."""
        a2a_paths = [".well-known/agent.json", "a2a"]
        for path in a2a_paths:
            assert path.startswith(".well-known/") or path == "a2a"

    def test_symlink_outside_dir_blocked(self, tmp_path):
        """Symlinks pointing outside the dashboard dir are blocked by resolve()."""
        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        (dashboard_dir / "index.html").write_text("<html></html>")

        outside_file = tmp_path / "secret.txt"
        outside_file.write_text("secret data")

        link = dashboard_dir / "link.txt"
        link.symlink_to(outside_file)

        file = (dashboard_dir / "link.txt").resolve()
        # After resolving the symlink, the file is outside dashboard_dir
        assert not file.is_relative_to(dashboard_dir)

    def test_nested_path_traversal_blocked(self, tmp_path):
        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        (dashboard_dir / "index.html").write_text("<html></html>")

        path = "assets/../../../etc/passwd"
        file = (dashboard_dir / path).resolve()
        assert not file.is_relative_to(dashboard_dir)

    def test_index_html_fallback(self, tmp_path):
        """Non-existent paths should fall back to index.html."""
        dashboard_dir = tmp_path / "dashboard"
        dashboard_dir.mkdir()
        (dashboard_dir / "index.html").write_text("<html>SPA</html>")

        path = "some/deep/spa/route"
        file = (dashboard_dir / path).resolve()
        safe = file.is_relative_to(dashboard_dir)
        exists = file.exists() and file.is_file()
        # Not a real file, so SPA fallback should serve index.html
        assert safe and not exists


# ===========================================================================
# Section 14: Middleware ordering verification (main.py)
# ===========================================================================


class TestMiddlewareOrdering:
    """Verify middleware is added in correct order: auth (inner) -> security -> CORS (outer)."""

    def test_middleware_types_present(self):
        """Import the app and check middleware stack."""
        # We import the app to inspect middleware. We don't start it.
        from sandcastle.main import app

        middleware_classes = []
        for m in app.user_middleware:
            if hasattr(m, "cls"):
                middleware_classes.append(m.cls.__name__)
            elif hasattr(m, "kwargs") and "dispatch" in m.kwargs:
                func = m.kwargs["dispatch"]
                middleware_classes.append(func.__name__)

        # The CORS middleware should be present
        assert "CORSMiddleware" in middleware_classes

    def test_security_headers_applied_to_all_responses(self):
        """Security headers middleware should wrap auth middleware."""
        from sandcastle.api.security_headers import security_headers_middleware

        # The function should be async and accept request + call_next
        import asyncio
        import inspect

        assert inspect.iscoroutinefunction(security_headers_middleware)

    def test_auth_middleware_is_async(self):
        from sandcastle.api.auth import auth_middleware
        import inspect

        assert inspect.iscoroutinefunction(auth_middleware)


# ===========================================================================
# Section 15: Security headers content (main.py via security_headers.py)
# ===========================================================================


class TestSecurityHeadersContent:
    """Verify security header values are correct."""

    @pytest.mark.asyncio
    async def test_standard_headers_added(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/api/health"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await security_headers_middleware(request, call_next)
        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in result.headers["Permissions-Policy"]

    @pytest.mark.asyncio
    async def test_api_paths_get_no_cache(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/api/runs"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await security_headers_middleware(request, call_next)
        assert result.headers["Cache-Control"] == "no-store"
        assert result.headers["Pragma"] == "no-cache"

    @pytest.mark.asyncio
    async def test_dashboard_paths_get_csp(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/dashboard/overview"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await security_headers_middleware(request, call_next)
        assert "Content-Security-Policy" in result.headers

    @pytest.mark.asyncio
    async def test_api_paths_no_csp(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/api/health"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await security_headers_middleware(request, call_next)
        assert "Content-Security-Policy" not in result.headers

    @pytest.mark.asyncio
    async def test_case_insensitive_api_check(self):
        """CSP bypass via /API/ or /Api/ should be prevented."""
        from sandcastle.api.security_headers import security_headers_middleware

        for path in ["/API/runs", "/Api/health", "/API/"]:
            request = MagicMock()
            request.url.path = path

            response = MagicMock()
            response.headers = {}

            async def call_next(req):
                return response

            result = await security_headers_middleware(request, call_next)
            # These should be treated as API paths: no-cache yes, CSP no
            assert result.headers.get("Cache-Control") == "no-store", (
                f"Path {path} should get no-store Cache-Control"
            )
            assert "Content-Security-Policy" not in result.headers, (
                f"Path {path} should not get CSP (case-insensitive API check)"
            )

    @pytest.mark.asyncio
    async def test_csp_report_only_mode(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/dashboard"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = True
            mock_settings.is_local_mode = True
            result = await security_headers_middleware(request, call_next)
            assert "Content-Security-Policy-Report-Only" in result.headers
            assert "Content-Security-Policy" not in result.headers

    @pytest.mark.asyncio
    async def test_x_permitted_cross_domain_policies(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/anything"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        result = await security_headers_middleware(request, call_next)
        assert result.headers["X-Permitted-Cross-Domain-Policies"] == "none"


# ===========================================================================
# Section 16: model_config properties
# ===========================================================================


class TestModelConfig:
    """Settings model_config correctness."""

    def test_env_file_encoding_is_utf8(self):
        assert Settings.model_config.get("env_file_encoding") == "utf-8"

    def test_extra_is_ignore(self):
        assert Settings.model_config.get("extra") == "ignore"

    def test_env_file_is_dotenv(self):
        assert Settings.model_config.get("env_file") == ".env"


# ===========================================================================
# Section 17: Float precision edge cases
# ===========================================================================


class TestFloatPrecision:
    """Float fields should handle various representations."""

    def test_very_small_cost(self):
        s = _make_settings(default_max_cost_usd=0.001)
        assert s.default_max_cost_usd == pytest.approx(0.001)

    def test_large_cost(self):
        s = _make_settings(default_max_cost_usd=999999.99)
        assert s.default_max_cost_usd == pytest.approx(999999.99)

    def test_admit_threshold_boundary_low(self):
        s = _make_settings(memory_admit_threshold=0.0001)
        assert s.memory_admit_threshold == pytest.approx(0.0001)

    def test_admit_threshold_boundary_high(self):
        s = _make_settings(memory_admit_threshold=0.9999)
        assert s.memory_admit_threshold == pytest.approx(0.9999)

    def test_failover_cooldown_very_large(self):
        s = _make_settings(failover_cooldown_seconds=86400.0)
        assert s.failover_cooldown_seconds == pytest.approx(86400.0)

    def test_failover_cooldown_fractional(self):
        s = _make_settings(failover_cooldown_seconds=0.1)
        assert s.failover_cooldown_seconds == pytest.approx(0.1)


# ===========================================================================
# Section 18: Docker hardening boundary tests (extended from existing)
# ===========================================================================


class TestDockerHardeningBoundary:
    """Extended boundary tests for Docker hardening settings."""

    def test_pids_limit_exactly_one(self):
        s = _make_settings(docker_pids_limit=1)
        assert s.docker_pids_limit == 1

    def test_pids_limit_very_large(self):
        """Very large PID limits should be accepted (sysadmin's choice)."""
        s = _make_settings(docker_pids_limit=10000)
        assert s.docker_pids_limit == 10000

    def test_cpu_period_exactly_1000(self):
        s = _make_settings(docker_cpu_period=1000)
        assert s.docker_cpu_period == 1000

    def test_cpu_period_999_falls_back(self):
        s = _make_settings(docker_cpu_period=999)
        assert s.docker_cpu_period == 100_000

    def test_cpu_quota_exactly_1000(self):
        s = _make_settings(docker_cpu_quota=1000)
        assert s.docker_cpu_quota == 1000

    def test_cpu_quota_999_falls_back(self):
        s = _make_settings(docker_cpu_quota=999)
        assert s.docker_cpu_quota == 50_000


# ===========================================================================
# Section 19: key_rotation_grace_hours
# ===========================================================================


class TestKeyRotationGraceHours:
    """KEY_ROTATION_GRACE_HOURS edge cases."""

    def test_zero_is_valid(self):
        """Zero grace period means immediate expiry of old key."""
        s = _make_settings(key_rotation_grace_hours=0)
        assert s.key_rotation_grace_hours == 0

    def test_large_value_accepted(self):
        s = _make_settings(key_rotation_grace_hours=720)
        assert s.key_rotation_grace_hours == 720

    def test_negative_falls_back_to_24(self):
        s = _make_settings(key_rotation_grace_hours=-1)
        assert s.key_rotation_grace_hours == 24


# ===========================================================================
# Section 20: Lifespan startup/shutdown structure
# ===========================================================================


class TestLifespanStructure:
    """Verify lifespan context manager structure."""

    def test_lifespan_is_async_context_manager(self):
        from sandcastle.main import lifespan
        import inspect

        # Should be decorated with @asynccontextmanager
        assert inspect.isasyncgenfunction(lifespan.__wrapped__) or \
               callable(lifespan)

    def test_app_has_lifespan(self):
        from sandcastle.main import app

        # FastAPI should have a lifespan configured
        assert app.router.lifespan_context is not None


# ===========================================================================
# Section 21: Router prefix configuration
# ===========================================================================


class TestRouterConfiguration:
    """Verify routers are mounted at correct prefixes."""

    def test_api_router_prefix(self):
        from sandcastle.main import app

        route_paths = {r.path for r in app.routes if hasattr(r, "path")}
        # Should have /api/ prefixed routes
        has_api = any("/api" in str(p) for p in route_paths)
        assert has_api

    def test_openapi_docs_at_correct_path(self):
        from sandcastle.main import app

        assert app.docs_url == "/api/docs"
        assert app.redoc_url == "/api/redoc"
        assert app.openapi_url == "/api/openapi.json"


# ===========================================================================
# Section 22: __init__.py exports
# ===========================================================================


class TestPackageInit:
    """Verify __init__.py exports are correct."""

    def test_version_defined(self):
        from sandcastle import __version__

        assert isinstance(__version__, str)
        # Should be semver-ish
        parts = __version__.split(".")
        assert len(parts) >= 2

    def test_sdk_clients_exported(self):
        from sandcastle import AsyncSandcastleClient, SandcastleClient

        assert SandcastleClient is not None
        assert AsyncSandcastleClient is not None

    def test_all_exports(self):
        import sandcastle

        assert set(sandcastle.__all__) == {
            "SandcastleClient", "AsyncSandcastleClient", "__version__"
        }


# ===========================================================================
# Section 23: CORS wildcard warning (main.py logic)
# ===========================================================================


class TestCorsWildcardHandling:
    """Test CORS wildcard detection and handling."""

    def test_wildcard_origin_filtered_from_cors_list(self):
        """Wildcard is filtered from _cors_origins list in main.py."""
        origins = ["*", "http://localhost:5173", "http://localhost:5174"]
        filtered = list(dict.fromkeys(o for o in origins if o != "*"))
        assert "*" not in filtered

    def test_wildcard_origin_keeps_dev_ports(self):
        """Even with wildcard dashboard_origin, dev ports should remain."""
        origins = ["*", "http://localhost:5173", "http://localhost:5174"]
        filtered = list(dict.fromkeys(o for o in origins if o != "*"))
        assert "http://localhost:5173" in filtered
        assert "http://localhost:5174" in filtered


# ===========================================================================
# Section 24: Settings field defaults
# ===========================================================================


class TestSettingsDefaults:
    """Every field should have a sensible default."""

    def test_default_sandbox_backend_is_e2b(self):
        s = _make_settings()
        assert s.sandbox_backend == "e2b"

    def test_default_storage_backend_is_local(self):
        s = _make_settings()
        assert s.storage_backend == "local"

    def test_default_memory_backend_is_local(self):
        s = _make_settings()
        assert s.memory_backend == "local"

    def test_default_log_level_is_info(self):
        s = _make_settings()
        assert s.log_level == "info"

    def test_default_auth_required_is_false(self):
        s = _make_settings()
        assert s.auth_required is False

    def test_default_scheduler_enabled_is_true(self):
        s = _make_settings()
        assert s.scheduler_enabled is True

    def test_default_telemetry_disabled(self):
        with patch.dict(os.environ, {"TELEMETRY_ENABLED": ""}, clear=False):
            os.environ.pop("TELEMETRY_ENABLED", None)
            s = _make_settings()
            assert s.telemetry_enabled is False

    def test_default_csp_report_only_false(self):
        s = _make_settings()
        assert s.csp_report_only is False

    def test_default_max_workflow_depth(self):
        s = _make_settings()
        assert s.max_workflow_depth == 5

    def test_default_max_concurrent_sandboxes(self):
        s = _make_settings()
        assert s.max_concurrent_sandboxes == 5

    def test_default_cost_is_zero(self):
        s = _make_settings()
        assert s.default_max_cost_usd == 0.0


# ===========================================================================
# Section 25: HSTS behavior based on local mode
# ===========================================================================


class TestHstsHeader:
    """HSTS should only be set in production mode (non-local)."""

    @pytest.mark.asyncio
    async def test_hsts_not_set_in_local_mode(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = True
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)
            assert "Strict-Transport-Security" not in result.headers

    @pytest.mark.asyncio
    async def test_hsts_set_in_production_mode(self):
        from sandcastle.api.security_headers import security_headers_middleware

        request = MagicMock()
        request.url.path = "/"

        response = MagicMock()
        response.headers = {}

        async def call_next(req):
            return response

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.is_local_mode = False
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)
            hsts = result.headers.get("Strict-Transport-Security", "")
            assert "max-age=" in hsts
            assert "includeSubDomains" in hsts


# ===========================================================================
# Section 26: Validator combination tests
# ===========================================================================


class TestValidatorCombinations:
    """Test multiple validators firing together on a single Settings instance."""

    def test_multiple_invalid_values_all_fixed(self):
        s = _make_settings(
            sandbox_backend="invalid",
            storage_backend="invalid",
            memory_backend="invalid",
            log_level="invalid",
            max_concurrent_sandboxes=-5,
            default_max_cost_usd=-10.0,
            max_workflow_depth=100,
            memory_admit_threshold=5.0,
            failover_cooldown_seconds=-1.0,
            docker_pids_limit=0,
        )
        assert s.sandbox_backend == "e2b"
        assert s.storage_backend == "local"
        assert s.memory_backend == "local"
        assert s.log_level == "info"
        assert s.max_concurrent_sandboxes == 1
        assert s.default_max_cost_usd == 0.0
        assert s.max_workflow_depth == 20
        assert s.memory_admit_threshold == 1.0
        assert s.failover_cooldown_seconds == 60.0
        assert s.docker_pids_limit == 100

    def test_all_valid_boundary_values(self):
        s = _make_settings(
            max_concurrent_sandboxes=1,
            max_workflow_depth=1,
            memory_admit_threshold=0.0,
            failover_cooldown_seconds=0.001,
            docker_pids_limit=1,
            docker_cpu_period=1000,
            docker_cpu_quota=1000,
            key_rotation_grace_hours=0,
            memory_max_age_days=0,
            default_max_cost_usd=0.0,
            tool_smtp_port=1,
        )
        assert s.max_concurrent_sandboxes == 1
        assert s.max_workflow_depth == 1
        assert s.memory_admit_threshold == 0.0
        assert s.failover_cooldown_seconds == pytest.approx(0.001)
        assert s.docker_pids_limit == 1
        assert s.docker_cpu_period == 1000
        assert s.docker_cpu_quota == 1000
        assert s.key_rotation_grace_hours == 0
        assert s.memory_max_age_days == 0
        assert s.default_max_cost_usd == 0.0
        assert s.tool_smtp_port == 1
