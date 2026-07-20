"""Regression tests for tenant guards used by protocol handlers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandcastle.config import settings


@pytest.mark.parametrize(
    ("helper_module", "helper_name", "path"),
    [
        ("sandcastle.api.a2a", "_get_tenant_id_safe", "/a2a"),
        ("sandcastle.api.agui", "_get_tenant_id_safe", "/api/agui/stream/run"),
    ],
)
def test_protocol_tenant_helpers_fail_closed_without_auth_state(
    monkeypatch, helper_module, helper_name, path
):
    module = __import__(helper_module, fromlist=[helper_name])
    request = SimpleNamespace(state=SimpleNamespace(), url=SimpleNamespace(path=path))
    monkeypatch.setattr(settings, "auth_required", True)

    with pytest.raises(RuntimeError, match="Auth middleware did not run"):
        getattr(module, helper_name)(request)
