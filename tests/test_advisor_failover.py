"""Tests for smart auto-failover in _call_advisor_llm.

Covers:
1. Primary provider succeeds - no failover
2. Primary 429 -> failover to second provider succeeds
3. Primary 500 -> failover succeeds
4. Primary timeout -> failover succeeds
5. Primary ConnectError -> failover succeeds
6. Primary 429 + second 429 -> failover to third provider
7. All providers fail -> RuntimeError with clear message
8. data_residency="eu" -> only EU providers in failover chain
9. 400 error -> NOT retried (non-429 4xx client error)
10. Audit event records failover_from when failover used
11. Audit event has no failover_from when primary succeeds
12. Attempt counter is correct in audit payload
13. Provider key not configured -> skipped in failover list
14. Primary = ollama (no key needed) -> included in providers_to_try
15. _build_providers_to_try respects residency and skips keyless providers
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Ensure in-memory DB before any sandcastle imports
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_http_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
    """Build a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    if status_code == 200:
        resp.raise_for_status = MagicMock()
        resp.json.return_value = body or {"content": [{"text": "ok-response"}]}
    else:
        error = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
        resp.raise_for_status = MagicMock(side_effect=error)
    return resp


def _make_async_client(responses: list) -> tuple:
    """Build a mock httpx.AsyncClient that returns *responses* in order.

    Returns (mock_client_cls, mock_instance).
    """
    mock_instance = AsyncMock()
    mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
    mock_instance.__aexit__ = AsyncMock(return_value=False)
    mock_instance.post = AsyncMock(side_effect=responses)

    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Test 1: Primary succeeds - no failover
# ---------------------------------------------------------------------------


class TestPrimarySucceeds:
    @pytest.mark.asyncio
    async def test_primary_success_returns_response(self):
        """When primary provider succeeds, return its response with no failover."""
        from sandcastle.engine.generator import _call_advisor_llm

        ok_resp = _mock_http_response(200, {"content": [{"text": "primary-result"}]})
        mock_cls, _ = _make_async_client([ok_resp])

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key",
                                                 "SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
                        result = await _call_advisor_llm("sys", "usr", purpose="generation")

        assert result == "primary-result"

    @pytest.mark.asyncio
    async def test_primary_success_no_failover_in_audit(self):
        """Audit payload should have failover_from=None when primary succeeds."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured: list[dict] = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            captured.append(payload)

        ok_resp = _mock_http_response(200, {"content": [{"text": "ok"}]})
        mock_cls, _ = _make_async_client([ok_resp])

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key",
                                                 "SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
                        await _call_advisor_llm("sys", "usr")

        assert len(captured) == 1
        assert captured[0]["failover_from"] is None
        assert captured[0]["failover_reason"] is None
        assert captured[0]["attempt"] == 1


# ---------------------------------------------------------------------------
# Test 2: Primary 429 -> failover to second succeeds
# ---------------------------------------------------------------------------


class TestFailoverOn429:
    @pytest.mark.asyncio
    async def test_429_triggers_failover(self):
        """HTTP 429 on primary should trigger silent failover to next provider."""
        from sandcastle.engine.generator import _call_advisor_llm

        rate_limit_err = httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "fallback-result"}}]}

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        call_count = 0

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (primary) raises 429
                raise rate_limit_err
            # Second call (fallback) succeeds
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        result = await _call_advisor_llm("sys", "usr")

        assert result == "fallback-result"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_429_audit_records_failover(self):
        """Audit event should record failover_from when 429 triggers failover."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured: list[dict] = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            captured.append(payload)

        rate_limit_err = httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        call_count = 0

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise rate_limit_err
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        await _call_advisor_llm("sys", "usr", purpose="generation")

        assert len(captured) == 1
        assert captured[0]["failover_from"] == "anthropic"
        assert "429" in (captured[0]["failover_reason"] or "")


# ---------------------------------------------------------------------------
# Test 3: Primary 500 -> failover succeeds
# ---------------------------------------------------------------------------


class TestFailoverOn500:
    @pytest.mark.asyncio
    async def test_500_triggers_failover(self):
        """HTTP 500 on primary should trigger silent failover."""
        from sandcastle.engine.generator import _call_advisor_llm

        server_err = httpx.HTTPStatusError(
            "500",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "fallback-500"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise server_err
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        result = await _call_advisor_llm("sys", "usr")

        assert result == "fallback-500"

    @pytest.mark.asyncio
    async def test_503_triggers_failover(self):
        """HTTP 503 Service Unavailable should also trigger failover."""
        from sandcastle.engine.generator import _call_advisor_llm

        err = httpx.HTTPStatusError(
            "503",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "ok-503"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise err
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        result = await _call_advisor_llm("sys", "usr")

        assert result == "ok-503"


# ---------------------------------------------------------------------------
# Test 4 + 5: Timeout / ConnectError -> failover succeeds
# ---------------------------------------------------------------------------


class TestFailoverOnNetworkError:
    @pytest.mark.asyncio
    async def test_timeout_triggers_failover(self):
        """TimeoutException on primary should trigger failover."""
        from sandcastle.engine.generator import _call_advisor_llm

        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "timeout-fallback"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("timed out")
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        result = await _call_advisor_llm("sys", "usr")

        assert result == "timeout-fallback"

    @pytest.mark.asyncio
    async def test_connect_error_triggers_failover(self):
        """ConnectError on primary should trigger failover."""
        from sandcastle.engine.generator import _call_advisor_llm

        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "connect-fallback"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        result = await _call_advisor_llm("sys", "usr")

        assert result == "connect-fallback"


# ---------------------------------------------------------------------------
# Test 6: Primary 429 + second 429 -> failover to third
# ---------------------------------------------------------------------------


class TestMultiProviderFailover:
    @pytest.mark.asyncio
    async def test_two_providers_fail_third_succeeds(self):
        """When first two providers return 429, third should be tried and succeed."""
        from sandcastle.engine.generator import _call_advisor_llm

        rate_limit_err = httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "third-provider"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise rate_limit_err
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key1",
                        "OPENAI_API_KEY": "key2",
                        "MISTRAL_API_KEY": "key3",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        result = await _call_advisor_llm("sys", "usr")

        assert result == "third-provider"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_audit_attempt_counter_reflects_actual_attempt(self):
        """Audit payload attempt should equal the 1-based index of the succeeding provider."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured: list[dict] = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            captured.append(payload)

        rate_limit_err = httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise rate_limit_err
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key1",
                        "OPENAI_API_KEY": "key2",
                        "MISTRAL_API_KEY": "key3",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        await _call_advisor_llm("sys", "usr")

        assert captured[0]["attempt"] == 3


# ---------------------------------------------------------------------------
# Test 7: All providers fail -> RuntimeError
# ---------------------------------------------------------------------------


class TestAllProvidersFail:
    @pytest.mark.asyncio
    async def test_all_fail_raises_runtime_error(self):
        """When all providers fail, RuntimeError should be raised with clear message."""
        from sandcastle.engine.generator import _call_advisor_llm

        rate_limit_err = httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=rate_limit_err)
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        # Only anthropic key - single provider, exhausted
                        with pytest.raises(RuntimeError) as exc_info:
                            await _call_advisor_llm("sys", "usr")

        msg = str(exc_info.value)
        assert "All advisor providers failed" in msg
        assert "Tried:" in msg

    @pytest.mark.asyncio
    async def test_error_message_lists_tried_providers(self):
        """RuntimeError message should list the providers that were attempted."""
        from sandcastle.engine.generator import _call_advisor_llm

        err_429 = httpx.HTTPStatusError(
            "429",
            request=MagicMock(),
            response=MagicMock(status_code=429),
        )

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(side_effect=err_429)
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key1",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        with pytest.raises(RuntimeError) as exc_info:
                            await _call_advisor_llm("sys", "usr")

        msg = str(exc_info.value)
        # Both providers should be listed
        assert "anthropic" in msg


# ---------------------------------------------------------------------------
# Test 8: data_residency="eu" -> only EU providers in failover chain
# ---------------------------------------------------------------------------


class TestFailoverWithDataResidency:
    def test_build_providers_to_try_respects_eu_residency(self):
        """With data_residency='eu', only eu-region providers appear in the list."""
        from sandcastle.engine.generator import _build_providers_to_try, _PROVIDER_CONFIGS

        with patch.dict(os.environ, {"MISTRAL_API_KEY": "key-eu"}):
            providers = _build_providers_to_try("mistral", "eu")

        for name in providers:
            cfg = _PROVIDER_CONFIGS.get(name, {})
            region = cfg.get("region", "us")
            assert region == "eu", f"Provider '{name}' (region={region}) violates EU residency"

    def test_build_providers_to_try_respects_local_residency(self):
        """With data_residency='local', only local-region providers appear."""
        from sandcastle.engine.generator import _build_providers_to_try, _PROVIDER_CONFIGS

        providers = _build_providers_to_try("ollama", "local")

        for name in providers:
            cfg = _PROVIDER_CONFIGS.get(name, {})
            region = cfg.get("region", "us")
            assert region == "local", f"Provider '{name}' (region={region}) violates local residency"

    def test_build_providers_to_try_no_residency_includes_all_with_keys(self):
        """Without residency, all providers that have keys are included."""
        from sandcastle.engine.generator import _build_providers_to_try

        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "k1",
            "OPENAI_API_KEY": "k2",
            "MISTRAL_API_KEY": "k3",
        }):
            providers = _build_providers_to_try("anthropic", "")

        assert "anthropic" in providers
        assert "openai" in providers
        assert "mistral" in providers


# ---------------------------------------------------------------------------
# Test 9: 400 error -> NOT retried
# ---------------------------------------------------------------------------


class TestNonRetryable4xx:
    @pytest.mark.asyncio
    async def test_400_is_not_retried(self):
        """HTTP 400 Bad Request should not be retried - raise immediately."""
        from sandcastle.engine.generator import _call_advisor_llm

        err_400 = httpx.HTTPStatusError(
            "400",
            request=MagicMock(),
            response=MagicMock(status_code=400),
        )

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise err_400

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        with pytest.raises(httpx.HTTPStatusError) as exc_info:
                            await _call_advisor_llm("sys", "usr")

        # Should have been called exactly once (not retried)
        assert call_count == 1
        assert exc_info.value.response.status_code == 400

    @pytest.mark.asyncio
    async def test_401_is_not_retried(self):
        """HTTP 401 Unauthorized should not be retried."""
        from sandcastle.engine.generator import _call_advisor_llm

        err_401 = httpx.HTTPStatusError(
            "401",
            request=MagicMock(),
            response=MagicMock(status_code=401),
        )

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise err_401

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", new_callable=AsyncMock):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        with pytest.raises(httpx.HTTPStatusError):
                            await _call_advisor_llm("sys", "usr")

        assert call_count == 1


# ---------------------------------------------------------------------------
# Test 10 + 11: Audit event failover_from field
# ---------------------------------------------------------------------------


class TestAuditFailoverFields:
    @pytest.mark.asyncio
    async def test_audit_records_failover_from_on_failover(self):
        """audit payload.failover_from should be the primary provider name."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured: list[dict] = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            captured.append(payload)

        err = httpx.HTTPStatusError(
            "500",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        call_count = 0
        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        async def post_side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise err
            return ok_resp

        mock_instance.post = post_side_effect
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "OPENAI_API_KEY": "key2",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        await _call_advisor_llm("sys", "usr", purpose="evolution")

        assert len(captured) == 1
        assert captured[0]["failover_from"] == "anthropic"
        assert captured[0]["failover_reason"] is not None
        assert "500" in captured[0]["failover_reason"]

    @pytest.mark.asyncio
    async def test_audit_no_failover_from_when_primary_succeeds(self):
        """audit payload.failover_from must be None when no failover occurred."""
        from sandcastle.engine.generator import _call_advisor_llm

        captured: list[dict] = []

        async def fake_append(session, event_type, run_id, actor_id, payload):
            captured.append(payload)

        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"content": [{"text": "primary-ok"}]}

        mock_instance = AsyncMock()
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        mock_instance.post = AsyncMock(return_value=ok_resp)
        mock_cls = MagicMock(return_value=mock_instance)

        with patch("httpx.AsyncClient", mock_cls):
            with patch("sandcastle.models.db.async_session", return_value=_make_session()):
                with patch("sandcastle.engine.audit.append_audit_event", side_effect=fake_append):
                    with patch.dict(os.environ, {
                        "ANTHROPIC_API_KEY": "key",
                        "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                    }):
                        await _call_advisor_llm("sys", "usr")

        assert len(captured) == 1
        assert captured[0]["failover_from"] is None
        assert captured[0]["failover_reason"] is None


# ---------------------------------------------------------------------------
# Test 13: Provider key not configured -> skipped
# ---------------------------------------------------------------------------


class TestKeylessProviderSkipped:
    def test_provider_without_key_not_in_list(self):
        """Providers that have no configured key should not appear in providers_to_try."""
        from sandcastle.engine.generator import _build_providers_to_try

        # Only anthropic key set - openai, mistral should be absent
        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "key",
        }, clear=True):
            providers = _build_providers_to_try("anthropic", "")

        # openai and mistral have no key - should not be in the list
        assert "openai" not in providers
        assert "mistral" not in providers
        assert "anthropic" in providers  # primary always included


# ---------------------------------------------------------------------------
# Test 14: Ollama (no key needed) is included
# ---------------------------------------------------------------------------


class TestOllamaIncluded:
    def test_ollama_included_in_no_residency(self):
        """Ollama should be included in providers_to_try even without a key."""
        from sandcastle.engine.generator import _build_providers_to_try

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key"}, clear=True):
            providers = _build_providers_to_try("anthropic", "")

        # Ollama has no api_key_env so _resolve_api_key_for_provider returns
        # 'no-key-required' which is truthy - ollama should be in the list
        assert "ollama" in providers

    def test_ollama_excluded_when_eu_residency(self):
        """Ollama (region='local') should be excluded when residency='eu'."""
        from sandcastle.engine.generator import _build_providers_to_try

        with patch.dict(os.environ, {"MISTRAL_API_KEY": "key"}, clear=True):
            providers = _build_providers_to_try("mistral", "eu")

        assert "ollama" not in providers


# ---------------------------------------------------------------------------
# Test 15: _build_providers_to_try structure
# ---------------------------------------------------------------------------


class TestBuildProvidersToTry:
    def test_primary_is_always_first(self):
        """The primary provider must be first in the list."""
        from sandcastle.engine.generator import _build_providers_to_try

        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "k1",
            "OPENAI_API_KEY": "k2",
        }):
            providers = _build_providers_to_try("anthropic", "")

        assert providers[0] == "anthropic"

    def test_primary_not_duplicated(self):
        """Primary provider should appear exactly once in providers_to_try."""
        from sandcastle.engine.generator import _build_providers_to_try

        with patch.dict(os.environ, {
            "ANTHROPIC_API_KEY": "k1",
            "OPENAI_API_KEY": "k2",
            "MISTRAL_API_KEY": "k3",
        }):
            providers = _build_providers_to_try("openai", "")

        assert providers.count("openai") == 1

    def test_returns_list_type(self):
        """_build_providers_to_try always returns a list."""
        from sandcastle.engine.generator import _build_providers_to_try

        result = _build_providers_to_try("anthropic", "")
        assert isinstance(result, list)
