"""Ensure dashboard API and SSE paths exist in the FastAPI route table."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sandcastle.main import app

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_SRC = REPO_ROOT / "dashboard" / "src"

# Test fixtures deliberately use fake paths to exercise client error handling;
# they are not dashboard-to-backend contracts.
TEST_PATH_PARTS = {"__tests__"}

# Intentionally absent endpoints belong here as (METHOD, path) tuples. Keep
# this empty: all live dashboard paths should be backed by FastAPI routes.
INTENTIONALLY_MISSING: frozenset[tuple[str, str]] = frozenset()

API_CALL_RE = re.compile(
    r"""
    api\.(?P<method>get|post|put|patch|delete)
    (?:\s*<[^()]*?>)?
    \s*\(\s*
    (?:
        "(?P<double>[^"\n]+)"
        | '(?P<single>[^'\n]+)'
        | `(?P<template>[^`]+)`
    )
    """,
    re.VERBOSE,
)
SSE_URL_RE = re.compile(r"`\$\{API_BASE_URL\}(?P<template>/[^`]*)`")
PARAM_RE = re.compile(r"\{[^/]+\}")


@dataclass(frozen=True)
class DashboardPath:
    method: str
    path: str
    partial: bool
    source: Path


def _normalise_path(path: str) -> str:
    path = path.split("?", maxsplit=1)[0]
    if path == "/api":
        path = "/"
    elif path.startswith("/api/"):
        path = path[4:]
    path = PARAM_RE.sub("{}", path)
    return path.rstrip("/") or "/"


def _literal_path(value: str) -> tuple[str, bool] | None:
    path, separator, _ = value.partition("${")
    if not path.startswith("/"):
        return None
    return _normalise_path(path), bool(separator)


def _source_files() -> Iterator[Path]:
    for path in DASHBOARD_SRC.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if TEST_PATH_PARTS.intersection(path.parts) or ".test." in path.name:
            continue
        yield path


def _dashboard_paths() -> Iterator[DashboardPath]:
    for source in _source_files():
        content = source.read_text(encoding="utf-8")
        for match in API_CALL_RE.finditer(content):
            value = next(
                item for item in match.group("double", "single", "template") if item is not None
            )
            literal = _literal_path(value)
            if literal:
                path, partial = literal
                yield DashboardPath(match.group("method").upper(), path, partial, source)

        # Fetch-based SSE endpoints use API_BASE_URL directly so custom auth
        # headers can be sent. Generic `useSSE(path)` calls are skipped because
        # they have no literal path to verify here.
        for match in SSE_URL_RE.finditer(content):
            literal = _literal_path(match.group("template"))
            if literal:
                path, partial = literal
                yield DashboardPath("GET", path, partial, source)


def _fastapi_paths() -> set[tuple[str, str]]:
    """Collect (method, path) pairs from the app's OpenAPI schema.

    The schema is used instead of ``app.routes`` because FastAPI 0.139+
    represents included routers as ``_IncludedRouter`` objects rather than
    flattened ``APIRoute`` entries, so route-table introspection is not
    version-stable. The OpenAPI document covers every schema-visible API
    route on both generations.
    """
    paths: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            paths.add((method.upper(), _normalise_path(path)))
    return paths


def _has_route(path: DashboardPath, routes: set[tuple[str, str]]) -> bool:
    if path.partial:
        return any(
            method == path.method and route_path.startswith(path.path)
            for method, route_path in routes
        )
    return (path.method, path.path) in routes


def test_dashboard_paths_have_fastapi_routes() -> None:
    routes = _fastapi_paths()
    missing = sorted(
        {
            (path.method, path.path, path.source.relative_to(REPO_ROOT))
            for path in _dashboard_paths()
            if (path.method, path.path) not in INTENTIONALLY_MISSING and not _has_route(path, routes)
        }
    )

    assert not missing, "Dashboard paths missing from FastAPI route table:\n" + "\n".join(
        f"{method:6} {path:40} ({source})" for method, path, source in missing
    )
