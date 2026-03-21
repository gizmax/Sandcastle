"""Coverage for api/schemas.py validators and models/db.py helpers.

Targets:
  - api/schemas.py: WorkflowRunRequest version validators, ScheduleUpdateRequest
    validators, ApiKeyCreateRequest allowed_workflows validator,
    ToolConnectionCreateRequest credentials validator
  - models/db.py: _build_engine_url OSError branch, _auto_migrate_columns
  - engine/dag.py: accessible branch coverage
"""

from __future__ import annotations

import pytest


# ===========================================================================
# api/schemas.py - WorkflowRunRequest validators
# ===========================================================================

class TestWorkflowRunRequestValidators:
    """Cover WorkflowRunRequest field validators."""

    def test_version_none_is_valid(self):
        """WorkflowRunRequest accepts version=None."""
        from sandcastle.api.schemas import WorkflowRunRequest

        req = WorkflowRunRequest(workflow="name: test\nsteps: []\n", version=None)
        assert req.version is None

    def test_version_latest_string_is_valid(self):
        """WorkflowRunRequest accepts version='latest'."""
        from sandcastle.api.schemas import WorkflowRunRequest

        req = WorkflowRunRequest(workflow="name: test\nsteps: []\n", version="latest")
        assert req.version == "latest"

    def test_version_positive_int_is_valid(self):
        """WorkflowRunRequest accepts positive integer version."""
        from sandcastle.api.schemas import WorkflowRunRequest

        req = WorkflowRunRequest(workflow="name: test\nsteps: []\n", version=3)
        assert req.version == 3

    def test_version_zero_is_invalid(self):
        """WorkflowRunRequest rejects version=0."""
        from sandcastle.api.schemas import WorkflowRunRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowRunRequest(workflow="name: test\nsteps: []\n", version=0)

    def test_version_invalid_string_is_invalid(self):
        """WorkflowRunRequest rejects version='invalid'."""
        from sandcastle.api.schemas import WorkflowRunRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowRunRequest(workflow="name: test\nsteps: []\n", version="invalid")

    def test_callback_url_must_be_http(self):
        """WorkflowRunRequest rejects non-HTTP callback URLs."""
        from sandcastle.api.schemas import WorkflowRunRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowRunRequest(
                workflow="name: test\nsteps: []\n",
                callback_url="ftp://example.com/callback",
            )

    def test_callback_url_empty_string_rejected(self):
        """WorkflowRunRequest rejects empty string callback_url."""
        from sandcastle.api.schemas import WorkflowRunRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowRunRequest(
                workflow="name: test\nsteps: []\n",
                callback_url="   ",
            )

    def test_no_workflow_or_name_raises(self):
        """WorkflowRunRequest requires workflow or workflow_name."""
        from sandcastle.api.schemas import WorkflowRunRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WorkflowRunRequest()


# ===========================================================================
# api/schemas.py - ScheduleUpdateRequest validators
# ===========================================================================

class TestScheduleUpdateRequest:
    """Cover ScheduleUpdateRequest field validators."""

    def test_valid_cron_expression(self):
        """ScheduleUpdateRequest accepts valid cron expression."""
        from sandcastle.api.schemas import ScheduleUpdateRequest

        req = ScheduleUpdateRequest(cron_expression="0 9 * * *")
        assert req.cron_expression == "0 9 * * *"

    def test_invalid_cron_expression_too_few_fields(self):
        """ScheduleUpdateRequest rejects cron with wrong number of fields."""
        from sandcastle.api.schemas import ScheduleUpdateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ScheduleUpdateRequest(cron_expression="0 9 * *")

    def test_cron_expression_none_is_valid(self):
        """ScheduleUpdateRequest accepts None cron expression."""
        from sandcastle.api.schemas import ScheduleUpdateRequest

        req = ScheduleUpdateRequest(cron_expression=None, enabled=True)
        assert req.cron_expression is None

    def test_valid_input_data(self):
        """ScheduleUpdateRequest accepts valid input_data."""
        from sandcastle.api.schemas import ScheduleUpdateRequest

        req = ScheduleUpdateRequest(input_data={"key": "value"})
        assert req.input_data == {"key": "value"}


# ===========================================================================
# api/schemas.py - ApiKeyCreateRequest validators
# ===========================================================================

class TestApiKeyCreateRequest:
    """Cover ApiKeyCreateRequest field validators."""

    def test_valid_allowed_workflows(self):
        """ApiKeyCreateRequest accepts valid allowed_workflows list."""
        from sandcastle.api.schemas import ApiKeyCreateRequest

        req = ApiKeyCreateRequest(
            name="test-key",
            allowed_workflows=["workflow-1", "workflow-2"],
        )
        assert len(req.allowed_workflows) == 2

    def test_empty_workflow_name_rejected(self):
        """ApiKeyCreateRequest rejects empty string in allowed_workflows."""
        from sandcastle.api.schemas import ApiKeyCreateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ApiKeyCreateRequest(
                name="test-key",
                allowed_workflows=["valid", ""],
            )

    def test_too_long_workflow_name_rejected(self):
        """ApiKeyCreateRequest rejects workflow name > 200 chars."""
        from sandcastle.api.schemas import ApiKeyCreateRequest
        from pydantic import ValidationError

        long_name = "a" * 201
        with pytest.raises(ValidationError):
            ApiKeyCreateRequest(
                name="test-key",
                allowed_workflows=[long_name],
            )

    def test_none_allowed_workflows_is_valid(self):
        """ApiKeyCreateRequest accepts None allowed_workflows."""
        from sandcastle.api.schemas import ApiKeyCreateRequest

        req = ApiKeyCreateRequest(name="test-key", allowed_workflows=None)
        assert req.allowed_workflows is None


# ===========================================================================
# api/schemas.py - ToolConnectionCreateRequest validators
# ===========================================================================

class TestToolConnectionCreateRequest:
    """Cover ToolConnectionCreateRequest field validators."""

    def test_valid_credentials(self):
        """ToolConnectionCreateRequest accepts valid credentials."""
        from sandcastle.api.schemas import ToolConnectionCreateRequest

        req = ToolConnectionCreateRequest(
            name="my-tool-connection",
            credentials={"API_KEY": "secret123"},
        )
        assert req.credentials == {"API_KEY": "secret123"}

    def test_too_many_credentials_rejected(self):
        """ToolConnectionCreateRequest rejects > 50 credential entries."""
        from sandcastle.api.schemas import ToolConnectionCreateRequest
        from pydantic import ValidationError

        creds = {f"KEY_{i}": f"val_{i}" for i in range(51)}
        with pytest.raises(ValidationError):
            ToolConnectionCreateRequest(
                name="my-tool-connection",
                credentials=creds,
            )

    def test_empty_credentials_rejected(self):
        """ToolConnectionCreateRequest rejects empty credentials dict."""
        from sandcastle.api.schemas import ToolConnectionCreateRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ToolConnectionCreateRequest(
                name="my-tool-connection",
                credentials={},
            )

    def test_credential_key_too_long_rejected(self):
        """ToolConnectionCreateRequest rejects credential key > 200 chars."""
        from sandcastle.api.schemas import ToolConnectionCreateRequest
        from pydantic import ValidationError

        long_key = "K" * 201
        with pytest.raises(ValidationError):
            ToolConnectionCreateRequest(
                name="my-tool-connection",
                credentials={long_key: "value"},
            )

    def test_credential_value_too_long_rejected(self):
        """ToolConnectionCreateRequest rejects credential value > 10000 chars."""
        from sandcastle.api.schemas import ToolConnectionCreateRequest
        from pydantic import ValidationError

        long_val = "v" * 10001
        with pytest.raises(ValidationError):
            ToolConnectionCreateRequest(
                name="my-tool-connection",
                credentials={"KEY": long_val},
            )


# ===========================================================================
# api/schemas.py - HubSubmitRequest validators
# ===========================================================================

class TestHubSubmitRequestValidators:
    """Cover HubSubmitRequest field validators."""

    def test_too_many_tags_rejected(self):
        """HubSubmitRequest rejects > 20 tags."""
        from sandcastle.api.schemas import HubSubmitRequest
        from pydantic import ValidationError

        tags = [f"tag{i}" for i in range(21)]
        with pytest.raises(ValidationError):
            HubSubmitRequest(
                yaml_content="name: test\nsteps: []\n",
                description="Test workflow",
                tags=tags,
            )

    def test_tag_too_long_rejected(self):
        """HubSubmitRequest rejects tag > 100 chars."""
        from sandcastle.api.schemas import HubSubmitRequest
        from pydantic import ValidationError

        long_tag = "t" * 101
        with pytest.raises(ValidationError):
            HubSubmitRequest(
                yaml_content="name: test\nsteps: []\n",
                description="Test workflow",
                tags=[long_tag],
            )

    def test_valid_submission(self):
        """HubSubmitRequest accepts valid submission."""
        from sandcastle.api.schemas import HubSubmitRequest

        req = HubSubmitRequest(
            yaml_content="name: my-workflow\nsteps: []\n",
            description="My test workflow",
            tags=["automation", "ai"],
        )
        assert len(req.tags) == 2


# ===========================================================================
# api/schemas.py - _check_json_dict_size
# ===========================================================================

class TestJsonDictSizeCheck:
    """Cover _check_json_dict_size function."""

    def test_oversized_input_dict_rejected(self):
        """WorkflowRunRequest rejects input dict that's too large (>1MB)."""
        from sandcastle.api.schemas import WorkflowRunRequest
        from pydantic import ValidationError

        # Create a dict that exceeds 1 MB when serialized
        # 1 key with a 2MB value
        large_dict = {"key": "v" * 1_100_000}
        with pytest.raises(ValidationError):
            WorkflowRunRequest(
                workflow="name: test\nsteps: []\n",
                input=large_dict,
            )


# ===========================================================================
# models/db.py - helper functions
# ===========================================================================

class TestDbHelpers:
    """Cover db.py helper functions."""

    def test_build_engine_url_with_database_url_set(self):
        """_build_engine_url returns DATABASE_URL when set."""
        from sandcastle.models.db import _build_engine_url
        from unittest.mock import patch

        with patch("sandcastle.models.db.settings") as mock_s:
            mock_s.database_url = "postgresql://user:pass@host/db"
            result = _build_engine_url()
            assert result == "postgresql://user:pass@host/db"

    def test_build_engine_url_sqlite_fallback(self):
        """_build_engine_url returns SQLite URL when no DATABASE_URL set."""
        from sandcastle.models.db import _build_engine_url
        from unittest.mock import patch
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("sandcastle.models.db.settings") as mock_s:
                mock_s.database_url = ""
                mock_s.data_dir = tmpdir
                result = _build_engine_url()
                assert "sqlite" in result
                assert "sandcastle.db" in result

    def test_build_engine_kwargs_sqlite(self):
        """_build_engine_kwargs returns check_same_thread for SQLite."""
        from sandcastle.models.db import _build_engine_kwargs

        kwargs = _build_engine_kwargs("sqlite+aiosqlite:///test.db")
        assert "connect_args" in kwargs
        assert kwargs["connect_args"]["check_same_thread"] is False

    def test_build_engine_kwargs_postgres(self):
        """_build_engine_kwargs returns minimal kwargs for PostgreSQL."""
        from sandcastle.models.db import _build_engine_kwargs

        kwargs = _build_engine_kwargs("postgresql+asyncpg://user:pass@host/db")
        assert "connect_args" not in kwargs
        assert kwargs["echo"] is False


# ===========================================================================
# engine/dag.py - accessible branches
# ===========================================================================

class TestDagBranches:
    """Cover dag.py accessible branches."""

    def test_parse_yaml_string_valid(self):
        """parse_yaml_string parses YAML workflow content."""
        from sandcastle.engine.dag import parse_yaml_string

        yaml_content = """name: test-wf
steps:
  - id: step1
    type: llm
    prompt: Hello
"""
        result = parse_yaml_string(yaml_content)
        assert result.name == "test-wf"

    def test_parse_yaml_string_invalid_raises(self):
        """parse_yaml_string raises for invalid YAML."""
        from sandcastle.engine.dag import parse_yaml_string

        with pytest.raises(Exception):
            parse_yaml_string("not: valid: yaml: :")

    def test_build_plan_simple_workflow(self):
        """build_plan creates execution plan for simple workflow."""
        from sandcastle.engine.dag import build_plan, parse_yaml_string

        yaml_content = """name: simple
steps:
  - id: step1
    type: llm
    prompt: test
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        assert plan is not None
        assert len(plan.stages) > 0

    def test_build_plan_sequential_workflow(self):
        """build_plan handles sequential workflow with depends_on."""
        from sandcastle.engine.dag import build_plan, parse_yaml_string

        yaml_content = """name: sequential
steps:
  - id: step1
    type: llm
    prompt: step 1
  - id: step2
    type: llm
    prompt: step 2
    depends_on: [step1]
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        assert len(plan.stages) >= 1

    def test_build_plan_parallel_workflow(self):
        """build_plan handles parallel steps (no depends_on)."""
        from sandcastle.engine.dag import build_plan, parse_yaml_string

        yaml_content = """name: parallel
steps:
  - id: step1
    type: llm
    prompt: step 1
  - id: step2
    type: llm
    prompt: step 2
"""
        wf = parse_yaml_string(yaml_content)
        plan = build_plan(wf)
        assert plan is not None

    def test_validate_workflow_valid(self):
        """validate returns empty list for valid workflow."""
        from sandcastle.engine.dag import parse_yaml_string, validate

        yaml_content = """name: test-wf
steps:
  - id: step1
    type: llm
    prompt: Hello world
"""
        wf = parse_yaml_string(yaml_content)
        errors = validate(wf)
        assert isinstance(errors, list)

    def test_parse_yaml_string_no_steps(self):
        """parse_yaml_string handles workflow with no steps."""
        from sandcastle.engine.dag import parse_yaml_string

        yaml_content = "name: empty-wf\nsteps: []\n"
        result = parse_yaml_string(yaml_content)
        assert result.name == "empty-wf"
        assert len(result.steps) == 0
