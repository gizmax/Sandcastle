"""Security headers middleware for Sandcastle.

Adds standard security headers to all responses and a Content-Security-Policy
header to non-API (dashboard) paths.
"""

from __future__ import annotations

from fastapi import Request
from starlette.responses import Response

from sandcastle.config import settings

# CSP for the dashboard SPA
_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://www.google-analytics.com https://pypi.org; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


async def security_headers_middleware(request: Request, call_next) -> Response:
    """Add security headers to every response."""
    response: Response = await call_next(request)

    # Normalize the path for case-insensitive prefix matching to prevent
    # CSP bypass via case variations like /API/... or /Api/...
    path_lower = request.url.path.lower()

    # Standard security headers (all paths)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    # Block Adobe Flash cross-domain policy loading
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    # HSTS: instruct browsers to only use HTTPS for future requests.
    # Only set when not running in local mode (production with TLS termination).
    if not settings.is_local_mode:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    # Prevent caching of API responses that may contain sensitive data
    if path_lower.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    # CSP only on dashboard paths (not /api) - use case-insensitive check
    if not path_lower.startswith("/api"):
        header_name = (
            "Content-Security-Policy-Report-Only"
            if settings.csp_report_only
            else "Content-Security-Policy"
        )
        response.headers[header_name] = _CSP_POLICY

    return response
