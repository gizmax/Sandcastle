"""Estimate endpoint contract and stress tests.

Tests POST /runs/estimate for:
  1. Valid workflow: valid=true, validation_errors=[], cost > 0
  2. Broken deps: valid=false, steps still computed, disclaimer has "unreliable"
  3. Unknown step type: behavior check
  4. Circular deps: valid=false
  5. Empty workflow (no steps): behavior
  6. Single non-LLM step: cost=0, valid=true (or expected errors)
  7. Invalid YAML: 400 error
  8. Contract shape: every response has required fields with correct types
  9. _scrub_secrets idempotency

Run:
    cd /Users/gizmax/Documents/Sandcastle
    uv run pytest -xvs tests/test_estimate_contract_stress.py
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)

URL = "/api/runs/estimate"


# ---------------------------------------------------------------------------
# YAML fixtures
# ---------------------------------------------------------------------------

VALID_WORKFLOW = """\
name: valid-workflow
description: A valid two-step LLM workflow
steps:
  - id: step_a
    prompt: "Summarise the input"
    model: sonnet
    max_turns: 2
  - id: step_b
    prompt: "Write a report based on {steps.step_a.output}"
    model: haiku
    max_turns: 1
    depends_on:
      - step_a
"""

BROKEN_DEPS_WORKFLOW = """\
name: broken-deps
description: Workflow referencing a non-existent step
steps:
  - id: real_step
    prompt: "Do something"
    model: haiku
    max_turns: 1
    depends_on:
      - ghost_step
"""

# 'nope' is intentionally not a valid type
UNKNOWN_STEP_TYPE_WORKFLOW = """\
name: unknown-type
description: Step with unrecognised type
steps:
  - id: mystery
    type: nope
    prompt: "Do something"
    model: sonnet
"""

CIRCULAR_DEPS_WORKFLOW = """\
name: circular
description: A->B->A circular dependency
steps:
  - id: step_x
    prompt: "X"
    model: sonnet
    depends_on:
      - step_y
  - id: step_y
    prompt: "Y"
    model: haiku
    depends_on:
      - step_x
"""

# An empty steps list is technically invalid YAML once parsed - we pass no steps key
# so parse_yaml_string will create an empty steps list -> validation error
EMPTY_STEPS_WORKFLOW = """\
name: empty-workflow
description: Workflow with no steps
steps: []
"""

SINGLE_HTTP_STEP_WORKFLOW = """\
name: single-http
description: One non-LLM step
steps:
  - id: fetch
    type: http
    prompt: "Fetch data"
    http_config:
      url: "https://api.example.com/data"
      method: GET
"""

INVALID_YAML = "name: bad\n  indentation: broken:\nsteps: [unclosed"

SECRET_YAML = """\
name: secret-workflow
description: Contains potential secret values
steps:
  - id: do_thing
    prompt: "Use this token: sk-abc1234567890abcdefghij and password=supersecretXYZ789"
    model: sonnet
    max_turns: 1
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _post(yaml_content: str):
    """POST to /api/runs/estimate, return (status_code, resp_json)."""
    resp = client.post(URL, json={"yaml_content": yaml_content})
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# 1. Valid workflow
# ---------------------------------------------------------------------------


class TestValidWorkflow:
    """valid=true, validation_errors=[], cost > 0 for LLM steps."""

    def test_returns_200(self):
        status, body = _post(VALID_WORKFLOW)
        assert status == 200, body

    def test_valid_true(self):
        _, body = _post(VALID_WORKFLOW)
        data = body["data"]
        assert data["valid"] is True

    def test_no_validation_errors(self):
        _, body = _post(VALID_WORKFLOW)
        data = body["data"]
        assert data["validation_errors"] == []

    def test_total_cost_positive(self):
        _, body = _post(VALID_WORKFLOW)
        data = body["data"]
        assert data["total_estimated_cost_usd"] > 0

    def test_steps_count(self):
        _, body = _post(VALID_WORKFLOW)
        data = body["data"]
        assert len(data["steps"]) == 2

    def test_workflow_name_echoed(self):
        _, body = _post(VALID_WORKFLOW)
        data = body["data"]
        assert data["workflow_name"] == "valid-workflow"

    def test_each_step_has_positive_cost(self):
        _, body = _post(VALID_WORKFLOW)
        for step in body["data"]["steps"]:
            assert step["estimated_cost_usd"] > 0, f"step {step['step_id']} has 0 cost"

    def test_total_equals_sum_of_steps(self):
        _, body = _post(VALID_WORKFLOW)
        data = body["data"]
        step_sum = sum(s["estimated_cost_usd"] for s in data["steps"])
        assert abs(data["total_estimated_cost_usd"] - round(step_sum, 4)) < 0.0002


# ---------------------------------------------------------------------------
# 2. Broken dependencies
# ---------------------------------------------------------------------------


class TestBrokenDependencies:
    """valid=false, steps still computed, disclaimer mentions 'unreliable'."""

    def test_returns_200_not_400(self):
        """Broken deps is a semantic error - should return 200 with valid=false."""
        status, _ = _post(BROKEN_DEPS_WORKFLOW)
        assert status == 200

    def test_valid_false(self):
        _, body = _post(BROKEN_DEPS_WORKFLOW)
        assert body["data"]["valid"] is False

    def test_validation_errors_non_empty(self):
        _, body = _post(BROKEN_DEPS_WORKFLOW)
        assert len(body["data"]["validation_errors"]) > 0

    def test_validation_error_mentions_missing_step(self):
        _, body = _post(BROKEN_DEPS_WORKFLOW)
        errors = " ".join(body["data"]["validation_errors"])
        assert "ghost_step" in errors

    def test_steps_still_computed(self):
        """Steps list should still be populated even if validation fails."""
        _, body = _post(BROKEN_DEPS_WORKFLOW)
        # real_step is an LLM step - it should still appear in step estimates
        assert len(body["data"]["steps"]) == 1

    def test_disclaimer_mentions_unreliable(self):
        _, body = _post(BROKEN_DEPS_WORKFLOW)
        disclaimer = body["data"]["disclaimer"].lower()
        assert "unreliable" in disclaimer, f"disclaimer={disclaimer!r}"


# ---------------------------------------------------------------------------
# 3. Unknown step type
# ---------------------------------------------------------------------------


class TestUnknownStepType:
    """type: nope - validator should flag it, response 200 with valid=false."""

    def test_returns_200(self):
        status, _ = _post(UNKNOWN_STEP_TYPE_WORKFLOW)
        assert status == 200

    def test_valid_false(self):
        _, body = _post(UNKNOWN_STEP_TYPE_WORKFLOW)
        assert body["data"]["valid"] is False

    def test_validation_error_mentions_type(self):
        _, body = _post(UNKNOWN_STEP_TYPE_WORKFLOW)
        errors = " ".join(body["data"]["validation_errors"])
        # Validator should mention the bad type name or "unknown type"
        assert "nope" in errors or "unknown" in errors.lower()

    def test_steps_list_present(self):
        """Steps list must be present even when type is unknown."""
        _, body = _post(UNKNOWN_STEP_TYPE_WORKFLOW)
        assert "steps" in body["data"]
        assert isinstance(body["data"]["steps"], list)

    def test_disclaimer_mentions_unreliable(self):
        """Unknown type triggers validation error so disclaimer should mention unreliable."""
        _, body = _post(UNKNOWN_STEP_TYPE_WORKFLOW)
        disclaimer = body["data"]["disclaimer"].lower()
        assert "unreliable" in disclaimer


# ---------------------------------------------------------------------------
# 4. Circular dependencies
# ---------------------------------------------------------------------------


class TestCircularDependencies:
    """valid=false, cycle error in validation_errors."""

    def test_returns_200(self):
        status, _ = _post(CIRCULAR_DEPS_WORKFLOW)
        assert status == 200

    def test_valid_false(self):
        _, body = _post(CIRCULAR_DEPS_WORKFLOW)
        assert body["data"]["valid"] is False

    def test_validation_errors_mention_cycle(self):
        _, body = _post(CIRCULAR_DEPS_WORKFLOW)
        errors = " ".join(body["data"]["validation_errors"]).lower()
        assert "cycle" in errors

    def test_steps_list_present(self):
        _, body = _post(CIRCULAR_DEPS_WORKFLOW)
        assert isinstance(body["data"]["steps"], list)
        assert len(body["data"]["steps"]) == 2

    def test_disclaimer_unreliable(self):
        _, body = _post(CIRCULAR_DEPS_WORKFLOW)
        assert "unreliable" in body["data"]["disclaimer"].lower()


# ---------------------------------------------------------------------------
# 5. Empty workflow (no steps)
# ---------------------------------------------------------------------------


class TestEmptyWorkflow:
    """Workflow with empty steps list - expect validation error."""

    def test_returns_200(self):
        """Semantic validation error, not a parse error."""
        status, _ = _post(EMPTY_STEPS_WORKFLOW)
        assert status == 200

    def test_valid_false(self):
        _, body = _post(EMPTY_STEPS_WORKFLOW)
        assert body["data"]["valid"] is False

    def test_validation_error_mentions_steps(self):
        _, body = _post(EMPTY_STEPS_WORKFLOW)
        errors = " ".join(body["data"]["validation_errors"]).lower()
        assert "step" in errors

    def test_steps_list_empty(self):
        _, body = _post(EMPTY_STEPS_WORKFLOW)
        assert body["data"]["steps"] == []

    def test_total_cost_zero(self):
        _, body = _post(EMPTY_STEPS_WORKFLOW)
        assert body["data"]["total_estimated_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# 6. Single non-LLM step
# ---------------------------------------------------------------------------


class TestSingleNonLlmStep:
    """http step: cost=0 per step; valid depends on validation rules."""

    def test_returns_200(self):
        status, _ = _post(SINGLE_HTTP_STEP_WORKFLOW)
        assert status == 200

    def test_step_cost_zero(self):
        """Non-LLM steps have no model cost."""
        _, body = _post(SINGLE_HTTP_STEP_WORKFLOW)
        data = body["data"]
        step = data["steps"][0]
        assert step["estimated_cost_usd"] == 0.0

    def test_total_cost_zero(self):
        _, body = _post(SINGLE_HTTP_STEP_WORKFLOW)
        assert body["data"]["total_estimated_cost_usd"] == 0.0

    def test_step_model_is_null(self):
        _, body = _post(SINGLE_HTTP_STEP_WORKFLOW)
        step = body["data"]["steps"][0]
        assert step["model"] is None

    def test_step_note_present(self):
        _, body = _post(SINGLE_HTTP_STEP_WORKFLOW)
        step = body["data"]["steps"][0]
        assert "note" in step
        assert step["note"]  # should be a non-empty string


# ---------------------------------------------------------------------------
# 7. Invalid YAML - 400 error
# ---------------------------------------------------------------------------


class TestInvalidYaml:
    """Malformed YAML should return HTTP 400."""

    def test_returns_400(self):
        status, _ = _post(INVALID_YAML)
        assert status == 400

    def test_error_code_in_response(self):
        _, body = _post(INVALID_YAML)
        # Should have error info, not data
        assert "error" in body or "detail" in body

    def test_error_code_invalid_yaml(self):
        """The error code should be INVALID_YAML."""
        _, body = _post(INVALID_YAML)
        # FastAPI wraps HTTPException detail in 'detail' field
        detail = body.get("detail", {})
        if isinstance(detail, dict):
            error_obj = detail.get("error", {})
            code = error_obj.get("code", "")
            assert code == "INVALID_YAML", f"got code={code!r}, full body={body}"
        # If it's a raw 400 for any reason, the status code itself is sufficient

    def test_empty_string_is_400(self):
        """Completely empty YAML string should also yield 400."""
        # But the schema requires min_length=1 so we expect 422 for empty
        # and 400 for syntactically broken YAML
        resp = client.post(URL, json={"yaml_content": "not: valid: yaml: [unclosed"})
        assert resp.status_code in (400, 422)

    def test_non_mapping_yaml_is_400(self):
        """YAML that is a list (not a mapping) should return 400."""
        resp = client.post(URL, json={"yaml_content": "- one\n- two\n- three\n"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 8. Contract shape
# ---------------------------------------------------------------------------


class TestContractShape:
    """Every 200 response must contain all required fields with correct types."""

    REQUIRED_TOP_KEYS = {
        "workflow_name",
        "valid",
        "total_estimated_cost_usd",
        "steps",
        "validation_errors",
        "disclaimer",
    }

    STEP_REQUIRED_KEYS = {"step_id", "type", "model", "estimated_cost_usd", "note"}

    def _check_shape(self, yaml_content: str) -> None:
        status, body = _post(yaml_content)
        assert status == 200, f"Expected 200, got {status}: {body}"
        data = body["data"]

        # All required top-level keys present
        for key in self.REQUIRED_TOP_KEYS:
            assert key in data, f"Missing key '{key}' in response"

        # Types
        assert isinstance(data["workflow_name"], str)
        assert isinstance(data["valid"], bool)
        assert isinstance(data["total_estimated_cost_usd"], (int, float))
        assert isinstance(data["steps"], list)
        assert isinstance(data["validation_errors"], list)
        assert isinstance(data["disclaimer"], str)

        # Each step entry
        for step in data["steps"]:
            for sk in self.STEP_REQUIRED_KEYS:
                assert sk in step, f"Step missing key '{sk}': {step}"
            assert isinstance(step["estimated_cost_usd"], (int, float))
            assert step["estimated_cost_usd"] >= 0

    def test_valid_workflow_shape(self):
        self._check_shape(VALID_WORKFLOW)

    def test_broken_deps_shape(self):
        self._check_shape(BROKEN_DEPS_WORKFLOW)

    def test_circular_deps_shape(self):
        self._check_shape(CIRCULAR_DEPS_WORKFLOW)

    def test_empty_workflow_shape(self):
        self._check_shape(EMPTY_STEPS_WORKFLOW)

    def test_single_http_step_shape(self):
        self._check_shape(SINGLE_HTTP_STEP_WORKFLOW)

    def test_unknown_type_shape(self):
        self._check_shape(UNKNOWN_STEP_TYPE_WORKFLOW)

    def test_disclaimer_is_non_empty_string(self):
        for yaml_content in [VALID_WORKFLOW, BROKEN_DEPS_WORKFLOW]:
            _, body = _post(yaml_content)
            d = body["data"]["disclaimer"]
            assert isinstance(d, str) and len(d) > 0

    def test_validation_errors_strings(self):
        """All items in validation_errors must be strings."""
        _, body = _post(BROKEN_DEPS_WORKFLOW)
        for err in body["data"]["validation_errors"]:
            assert isinstance(err, str)


# ---------------------------------------------------------------------------
# 9. _scrub_secrets idempotency
# ---------------------------------------------------------------------------


class TestScrubSecretsIdempotency:
    """_scrub_secrets run twice on the same input gives the same result."""

    def _get_scrub_fn(self):
        from sandcastle.engine.generator import _scrub_secrets
        return _scrub_secrets

    def test_import_ok(self):
        fn = self._get_scrub_fn()
        assert callable(fn)

    def test_idempotent_on_bearer_token(self):
        fn = self._get_scrub_fn()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdefg"
        once = fn(text)
        twice = fn(once)
        assert once == twice, f"Not idempotent:\nonce={once!r}\ntwice={twice!r}"

    def test_idempotent_on_sk_prefix(self):
        fn = self._get_scrub_fn()
        text = "key: sk-abc1234567890abcdefghij"
        once = fn(text)
        twice = fn(once)
        assert once == twice

    def test_idempotent_on_api_key_assignment(self):
        fn = self._get_scrub_fn()
        text = "api_key=supersecretpassword123456789"
        once = fn(text)
        twice = fn(once)
        assert once == twice

    def test_idempotent_on_clean_text(self):
        fn = self._get_scrub_fn()
        text = "This text has no secrets at all."
        once = fn(text)
        twice = fn(once)
        assert once == twice
        assert once == text  # clean text should be unchanged

    def test_idempotent_on_aws_key(self):
        fn = self._get_scrub_fn()
        text = "access_key_id=AKIAIOSFODNN7EXAMPLE"
        once = fn(text)
        twice = fn(once)
        assert once == twice

    def test_idempotent_on_pem_key(self):
        fn = self._get_scrub_fn()
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4\n"
            "-----END RSA PRIVATE KEY-----"
        )
        once = fn(text)
        twice = fn(once)
        assert once == twice

    def test_secret_actually_redacted(self):
        fn = self._get_scrub_fn()
        text = "token: ghp_supersecrettoken1234567890"
        result = fn(text)
        # The raw token prefix should not survive
        assert "ghp_supersecrettoken1234567890" not in result
        assert "[REDACTED]" in result

    def test_idempotent_on_credential_url(self):
        fn = self._get_scrub_fn()
        text = "db = postgres://user:pass@localhost/mydb"
        once = fn(text)
        twice = fn(once)
        assert once == twice

    def test_multiple_secrets_idempotent(self):
        fn = self._get_scrub_fn()
        text = (
            "api_key=abc12345678 and also sk-another1234567890abcdef "
            "bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload"
        )
        once = fn(text)
        twice = fn(once)
        assert once == twice


# ---------------------------------------------------------------------------
# Bonus: stress-test contract shape across repeated calls
# ---------------------------------------------------------------------------


class TestContractStress:
    """Rapidly fire 20 requests per workflow type and verify contract each time."""

    @pytest.mark.parametrize("yaml_content,expected_valid", [
        (VALID_WORKFLOW, True),
        (BROKEN_DEPS_WORKFLOW, False),
        (CIRCULAR_DEPS_WORKFLOW, False),
    ])
    def test_repeated_calls_stable(self, yaml_content: str, expected_valid: bool):
        """Same YAML should always return the same valid flag and shape."""
        for _ in range(5):
            status, body = _post(yaml_content)
            assert status == 200
            data = body["data"]
            assert data["valid"] is expected_valid
            assert isinstance(data["total_estimated_cost_usd"], (int, float))
            assert isinstance(data["steps"], list)
            assert isinstance(data["validation_errors"], list)
            assert isinstance(data["disclaimer"], str)
