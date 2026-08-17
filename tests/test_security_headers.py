"""Tests for CSP and security headers middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.api.security_headers import security_headers_middleware


def _make_request(path: str) -> MagicMock:
    """Create a mock request with the given path."""
    request = MagicMock()
    request.url.path = path
    return request


def _make_response() -> MagicMock:
    """Create a mock response with dict-like headers."""
    response = MagicMock()
    response.headers = {}
    return response


class TestSecurityHeaders:
    """Standard security headers should be present on all paths."""

    @pytest.mark.asyncio
    async def test_headers_on_api_path(self):
        request = _make_request("/api/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"
        assert result.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "camera=()" in result.headers["Permissions-Policy"]

    @pytest.mark.asyncio
    async def test_headers_on_dashboard_path(self):
        request = _make_request("/dashboard")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert result.headers["X-Frame-Options"] == "DENY"

    @pytest.mark.asyncio
    async def test_headers_on_root_path(self):
        request = _make_request("/")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert result.headers["X-Content-Type-Options"] == "nosniff"


class TestCSPRouting:
    """CSP should only be added to non-API paths."""

    @pytest.mark.asyncio
    async def test_csp_on_dashboard_path(self):
        request = _make_request("/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" in result.headers
        assert "default-src" in result.headers["Content-Security-Policy"]

    @pytest.mark.asyncio
    async def test_no_csp_on_api_path(self):
        request = _make_request("/api/workflows")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" not in result.headers
        assert "Content-Security-Policy-Report-Only" not in result.headers


class TestCSPReportOnly:
    """CSP_REPORT_ONLY setting should use report-only header."""

    @pytest.mark.asyncio
    async def test_report_only_mode(self):
        request = _make_request("/")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = True
            result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy-Report-Only" in result.headers
        assert "Content-Security-Policy" not in result.headers

    @pytest.mark.asyncio
    async def test_enforce_mode(self):
        request = _make_request("/")
        response = _make_response()
        call_next = AsyncMock(return_value=response)

        with patch("sandcastle.api.security_headers.settings") as mock_settings:
            mock_settings.csp_report_only = False
            result = await security_headers_middleware(request, call_next)

        assert "Content-Security-Policy" in result.headers
        assert "Content-Security-Policy-Report-Only" not in result.headers


class TestCSPPolicyContents:
    """Lock the directives that are easy to loosen by accident.

    An earlier fix for these exact two issues was written, stashed and lost;
    these assertions exist so the next one cannot disappear silently.
    """

    def _policy(self) -> str:
        from sandcastle.api.security_headers import _CSP_POLICY

        return _CSP_POLICY

    def _directive(self, name: str) -> str:
        for part in self._policy().split("; "):
            if part.startswith(f"{name} "):
                return part
        raise AssertionError(f"{name} missing from CSP policy")

    def test_script_src_has_no_unsafe_inline(self):
        # The dashboard is a Vite bundle with no inline <script>; allowing
        # 'unsafe-inline' here would negate XSS protection entirely.
        assert "'unsafe-inline'" not in self._directive("script-src")

    def test_script_src_is_self_only(self):
        assert self._directive("script-src") == "script-src 'self'"

    def test_google_fonts_origins_are_allowed(self):
        # index.html loads font CSS from googleapis and the files from gstatic.
        # Omitting either makes the browser block them and fall back silently.
        assert "https://fonts.googleapis.com" in self._directive("style-src")
        assert "https://fonts.gstatic.com" in self._directive("font-src")

    def test_style_src_still_allows_inline(self):
        # Tailwind and some UI components inject style attributes at runtime.
        assert "'unsafe-inline'" in self._directive("style-src")

    def test_dangerous_directives_stay_locked(self):
        assert self._directive("object-src") == "object-src 'none'"
        assert self._directive("frame-ancestors") == "frame-ancestors 'none'"
        assert self._directive("base-uri") == "base-uri 'self'"
