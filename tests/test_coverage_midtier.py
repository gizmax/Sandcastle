"""Coverage tests for mid-tier uncovered files.

Targets:
  - engine/storage.py    (S3Storage error paths, symlink traversal, create_storage)
  - engine/pdf.py        (chart generation, markdown elements, KPI tiles, truncation)
  - engine/eval.py       (assertion types, suite runner, DB persistence, helpers)
  - engine/generator.py  (strip_fencing, chat generation, explain_error, provider config)
  - engine/telemetry.py  (anonymize_event, scrub helpers, sentry init, set_workflow_context)
  - api/a2a.py           (agent card, json-rpc helpers, tasks/get, tasks/cancel, dispatch)
  - queue/scheduler.py   (add_schedule, remove_schedule, list_schedules, start/stop)
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# engine/storage.py
# ---------------------------------------------------------------------------


class TestLocalStorageEdgeCases:
    """Cover missed branches: symlink traversal, large writes, list prefix, delete missing."""

    @pytest.mark.asyncio
    async def test_write_too_large_raises(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage, _MAX_WRITE_SIZE

        storage = LocalStorage(base_dir=str(tmp_path))
        large = "x" * (_MAX_WRITE_SIZE + 1)
        with pytest.raises(ValueError, match="Content too large"):
            await storage.write("big.txt", large)

    @pytest.mark.asyncio
    async def test_path_traversal_raises(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="traversal"):
            await storage.write("../evil.txt", "data")

    @pytest.mark.asyncio
    async def test_empty_key_raises(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="empty"):
            await storage.read("")

    @pytest.mark.asyncio
    async def test_whitespace_key_raises(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="empty"):
            await storage.read("   ")

    @pytest.mark.asyncio
    async def test_control_char_key_raises(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        with pytest.raises(ValueError, match="control"):
            await storage.write("bad\x00key.txt", "data")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        # Should not raise
        await storage.delete("does-not-exist.txt")

    @pytest.mark.asyncio
    async def test_list_empty_prefix(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        await storage.write("a.txt", "1")
        await storage.write("b.txt", "2")
        results = await storage.list("")
        assert "a.txt" in results
        assert "b.txt" in results

    @pytest.mark.asyncio
    async def test_list_nonexistent_prefix_returns_empty(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        results = await storage.list("nonexistent/prefix")
        assert results == []

    @pytest.mark.asyncio
    async def test_list_with_prefix_filters(self, tmp_path):
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        await storage.write("foo/a.txt", "1")
        await storage.write("bar/b.txt", "2")
        results = await storage.list("foo")
        assert any("foo" in r for r in results)

    @pytest.mark.asyncio
    async def test_symlink_traversal_read_denied(self, tmp_path):
        """Symlinks pointing outside base_dir must be rejected on read."""
        from sandcastle.engine.storage import LocalStorage

        outside = tmp_path.parent / "outside_target.txt"
        outside.write_text("secret")
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()
        storage = LocalStorage(base_dir=str(storage_dir))

        link = storage_dir / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not supported")

        with pytest.raises(ValueError, match="traversal"):
            await storage.read("link.txt")

    @pytest.mark.asyncio
    async def test_write_exception_cleans_tmp(self, tmp_path):
        """If write fails mid-way, the temp file is cleaned up."""
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(base_dir=str(tmp_path))
        # Trigger an error by monkeypatching os.replace
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                await storage.write("fail.txt", "data")
        # Verify no .tmp files left
        assert not list(tmp_path.glob("*.tmp"))


class TestS3StorageValidation:
    """Cover S3Storage key validation and error paths (no real AWS)."""

    def test_empty_bucket_raises(self):
        from sandcastle.engine.storage import S3Storage

        with pytest.raises(ValueError, match="bucket"):
            S3Storage(bucket="")

    def test_whitespace_bucket_raises(self):
        from sandcastle.engine.storage import S3Storage

        with pytest.raises(ValueError, match="bucket"):
            S3Storage(bucket="   ")

    def test_safe_key_empty_raises(self):
        from sandcastle.engine.storage import S3Storage

        s = S3Storage(bucket="test-bucket")
        with pytest.raises(ValueError):
            s._safe_key("")

    def test_safe_key_too_long_raises(self):
        from sandcastle.engine.storage import S3Storage, _MAX_S3_KEY_LENGTH

        s = S3Storage(bucket="test-bucket")
        long_key = "a" * (_MAX_S3_KEY_LENGTH + 1)
        with pytest.raises(ValueError, match="exceeds maximum length"):
            s._safe_key(long_key)

    def test_safe_key_traversal_raises(self):
        from sandcastle.engine.storage import S3Storage

        s = S3Storage(bucket="test-bucket")
        with pytest.raises(ValueError, match="traversal"):
            s._safe_key("../../etc/passwd")

    def test_safe_key_absolute_path_raises(self):
        from sandcastle.engine.storage import S3Storage

        s = S3Storage(bucket="test-bucket")
        with pytest.raises(ValueError, match="traversal"):
            s._safe_key("/absolute/path")

    def test_safe_key_normalizes_dots(self):
        from sandcastle.engine.storage import S3Storage

        s = S3Storage(bucket="test-bucket")
        result = s._safe_key("a/./b")
        assert result == "a/b"

    def test_safe_key_control_char_raises(self):
        from sandcastle.engine.storage import S3Storage

        s = S3Storage(bucket="test-bucket")
        with pytest.raises(ValueError, match="control"):
            s._safe_key("bad\x01key")

    def test_s3_constructor_stores_params(self):
        from sandcastle.engine.storage import S3Storage

        s = S3Storage(
            bucket="my-bucket",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="AKID",
            aws_secret_access_key="SECRET",
        )
        assert s.bucket == "my-bucket"
        assert s.endpoint_url == "http://localhost:9000"
        assert s.aws_access_key_id == "AKID"


class TestS3StorageAsyncMethods:
    """Cover S3Storage read/write/list/delete with mocked aioboto3."""

    def _make_s3_mock(self):
        """Build a mock aioboto3 session and s3 client."""
        mock_s3 = AsyncMock()
        mock_client_cm = MagicMock()
        mock_client_cm.__aenter__ = AsyncMock(return_value=mock_s3)
        mock_client_cm.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.client.return_value = mock_client_cm
        return mock_session, mock_s3

    @pytest.mark.asyncio
    async def test_read_returns_content(self):
        from sandcastle.engine.storage import S3Storage

        mock_session, mock_s3 = self._make_s3_mock()
        body_mock = AsyncMock()
        body_mock.read.return_value = b"hello world"
        mock_s3.get_object.return_value = {"Body": body_mock}

        s = S3Storage(bucket="bucket")
        with patch("sandcastle.engine.storage.S3Storage._get_session", return_value=mock_session):
            result = await s.read("my/key.json")
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_read_raises_on_error(self):
        from sandcastle.engine.storage import S3Storage

        mock_session, mock_s3 = self._make_s3_mock()
        mock_s3.get_object.side_effect = RuntimeError("S3 error")
        # Make exceptions attribute not have NoSuchKey matching
        mock_s3.exceptions = MagicMock()
        mock_s3.exceptions.NoSuchKey = KeyError

        s = S3Storage(bucket="bucket")
        with patch("sandcastle.engine.storage.S3Storage._get_session", return_value=mock_session):
            with pytest.raises(RuntimeError, match="S3 error"):
                await s.read("my/key.json")

    @pytest.mark.asyncio
    async def test_write_calls_put_object(self):
        from sandcastle.engine.storage import S3Storage

        mock_session, mock_s3 = self._make_s3_mock()
        mock_s3.put_object.return_value = None

        s = S3Storage(bucket="bucket")
        with patch("sandcastle.engine.storage.S3Storage._get_session", return_value=mock_session):
            await s.write("my/key.json", '{"hello": "world"}')
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "bucket"
        assert call_kwargs["Key"] == "my/key.json"

    @pytest.mark.asyncio
    async def test_write_too_large_raises(self):
        from sandcastle.engine.storage import S3Storage, _MAX_WRITE_SIZE

        s = S3Storage(bucket="bucket")
        large = "x" * (_MAX_WRITE_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            await s.write("key.txt", large)

    @pytest.mark.asyncio
    async def test_list_returns_keys(self):
        from sandcastle.engine.storage import S3Storage

        mock_session, mock_s3 = self._make_s3_mock()

        # Simulate async paginator - paginator is a sync object, paginate returns async iter
        mock_page = {"Contents": [{"Key": "prefix/a.txt"}, {"Key": "prefix/b.txt"}]}
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = _async_iter([mock_page])
        # get_paginator is sync, not async
        mock_s3.get_paginator = MagicMock(return_value=mock_paginator)

        s = S3Storage(bucket="bucket")
        with patch("sandcastle.engine.storage.S3Storage._get_session", return_value=mock_session):
            result = await s.list("prefix/")
        assert "prefix/a.txt" in result
        assert "prefix/b.txt" in result

    @pytest.mark.asyncio
    async def test_delete_calls_delete_object(self):
        from sandcastle.engine.storage import S3Storage

        mock_session, mock_s3 = self._make_s3_mock()
        mock_s3.delete_object.return_value = None

        s = S3Storage(bucket="bucket")
        with patch("sandcastle.engine.storage.S3Storage._get_session", return_value=mock_session):
            await s.delete("my/key.json")
        mock_s3.delete_object.assert_called_once()


def _async_iter(items):
    """Helper: create async iterator from list of items."""
    class _AsyncIter:
        def __init__(self):
            self._items = iter(items)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._items)
            except StopIteration:
                raise StopAsyncIteration

    return _AsyncIter()


class TestCreateStorage:
    """Cover create_storage() factory - local and s3 branches."""

    def test_create_storage_local(self):
        from sandcastle.engine.storage import LocalStorage, create_storage

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.storage_backend = "local"
            storage = create_storage()
        assert isinstance(storage, LocalStorage)

    def test_create_storage_s3(self):
        from sandcastle.engine.storage import S3Storage, create_storage

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.storage_backend = "s3"
            mock_settings.storage_bucket = "my-bucket"
            mock_settings.storage_endpoint = ""
            mock_settings.aws_access_key_id = "AKID"
            mock_settings.aws_secret_access_key = "SECRET"
            storage = create_storage()
        assert isinstance(storage, S3Storage)
        assert storage.bucket == "my-bucket"

    def test_create_storage_s3_with_endpoint(self):
        from sandcastle.engine.storage import S3Storage, create_storage

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.storage_backend = "s3"
            mock_settings.storage_bucket = "my-bucket"
            mock_settings.storage_endpoint = "http://minio:9000"
            mock_settings.aws_access_key_id = ""
            mock_settings.aws_secret_access_key = ""
            storage = create_storage()
        assert isinstance(storage, S3Storage)
        assert storage.endpoint_url == "http://minio:9000"


# ---------------------------------------------------------------------------
# engine/eval.py
# ---------------------------------------------------------------------------


class TestCheckAssertion:
    """Cover all assertion type branches."""

    @pytest.mark.asyncio
    async def test_contains_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="contains", value="hello")
        result = await check_assertion(a, "say hello world")
        assert result.passed

    @pytest.mark.asyncio
    async def test_contains_fail(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="contains", value="missing")
        result = await check_assertion(a, "no match here")
        assert not result.passed
        assert "does not contain" in result.message

    @pytest.mark.asyncio
    async def test_not_contains_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="not_contains", value="bad")
        result = await check_assertion(a, "all good here")
        assert result.passed

    @pytest.mark.asyncio
    async def test_not_contains_fail(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="not_contains", value="bad")
        result = await check_assertion(a, "something bad here")
        assert not result.passed

    @pytest.mark.asyncio
    async def test_not_empty_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, "some output")
        assert result.passed

    @pytest.mark.asyncio
    async def test_not_empty_fail_none(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, None)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_not_empty_fail_empty_string(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, "")
        assert not result.passed

    @pytest.mark.asyncio
    async def test_not_empty_fail_empty_dict(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="not_empty")
        result = await check_assertion(a, {})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_equals_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="equals", value=42)
        result = await check_assertion(a, 42)
        assert result.passed

    @pytest.mark.asyncio
    async def test_equals_fail(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="equals", value=42)
        result = await check_assertion(a, 99)
        assert not result.passed

    @pytest.mark.asyncio
    async def test_regex_match_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="regex_match", value=r"\d{4}")
        result = await check_assertion(a, "year 2024")
        assert result.passed

    @pytest.mark.asyncio
    async def test_regex_match_fail(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="regex_match", value=r"\d{4}")
        result = await check_assertion(a, "no digits here")
        assert not result.passed

    @pytest.mark.asyncio
    async def test_regex_match_empty_pattern(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="regex_match", value="")
        result = await check_assertion(a, "anything")
        assert not result.passed

    @pytest.mark.asyncio
    async def test_max_cost_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="max_cost", value=1.0)
        result = await check_assertion(a, "output", run_metadata={"cost_usd": 0.5})
        assert result.passed

    @pytest.mark.asyncio
    async def test_max_cost_fail(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="max_cost", value=0.1)
        result = await check_assertion(a, "output", run_metadata={"cost_usd": 0.5})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_max_duration_pass(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="max_duration", value=60.0)
        result = await check_assertion(a, "output", run_metadata={"duration_seconds": 5.0})
        assert result.passed

    @pytest.mark.asyncio
    async def test_max_duration_fail(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="max_duration", value=5.0)
        result = await check_assertion(a, "output", run_metadata={"duration_seconds": 120.0})
        assert not result.passed

    @pytest.mark.asyncio
    async def test_unknown_assertion_type(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="nonexistent_type")
        result = await check_assertion(a, "output")
        assert not result.passed
        assert "Unknown assertion type" in result.message

    @pytest.mark.asyncio
    async def test_step_targeted_assertion(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="contains", value="step-output", step="my_step")
        result = await check_assertion(
            a, "global output", step_outputs={"my_step": "my step-output here"}
        )
        assert result.passed

    @pytest.mark.asyncio
    async def test_contains_truncates_long_output(self):
        from sandcastle.engine.eval import AssertionDef, check_assertion

        a = AssertionDef(type="contains", value="x")
        long_str = "x" * 300
        result = await check_assertion(a, long_str)
        assert result.passed
        assert len(result.actual) <= 200


class TestParseEvalSuite:
    """Cover YAML parsing paths."""

    def test_parse_valid_suite(self):
        from sandcastle.engine.eval import parse_eval_suite_string

        yaml_str = """
workflow: my-workflow
description: Test suite
concurrency: 2
cases:
  - name: case1
    input:
      query: hello
    assertions:
      - type: not_empty
      - type: contains
        value: world
    tags: [smoke]
"""
        suite = parse_eval_suite_string(yaml_str)
        assert suite.workflow == "my-workflow"
        assert suite.concurrency == 2
        assert len(suite.cases) == 1
        assert suite.cases[0].name == "case1"
        assert len(suite.cases[0].assertions) == 2
        assert suite.cases[0].tags == ["smoke"]

    def test_parse_missing_workflow_raises(self):
        from sandcastle.engine.eval import parse_eval_suite_string

        with pytest.raises(ValueError, match="workflow"):
            parse_eval_suite_string("cases: []")

    def test_parse_non_dict_raises(self):
        from sandcastle.engine.eval import parse_eval_suite_string

        with pytest.raises(ValueError, match="mapping"):
            parse_eval_suite_string("- list item")

    def test_parse_file_not_found(self, tmp_path):
        from sandcastle.engine.eval import parse_eval_suite

        with pytest.raises(FileNotFoundError):
            parse_eval_suite(tmp_path / "nonexistent.yaml")

    def test_parse_from_file(self, tmp_path):
        from sandcastle.engine.eval import parse_eval_suite

        f = tmp_path / "suite.yaml"
        f.write_text("workflow: test\ncases: []")
        suite = parse_eval_suite(str(f))
        assert suite.workflow == "test"

    def test_parse_default_case_name(self):
        from sandcastle.engine.eval import parse_eval_suite_string

        yaml_str = "workflow: wf\ncases:\n  - input: {q: hi}"
        suite = parse_eval_suite_string(yaml_str)
        assert suite.cases[0].name == "case_1"


class TestSummarizeOutput:
    """Cover _summarize_output helper."""

    def test_none_returns_none(self):
        from sandcastle.engine.eval import _summarize_output

        assert _summarize_output(None) is None

    def test_short_string_unchanged(self):
        from sandcastle.engine.eval import _summarize_output

        assert _summarize_output("hello") == "hello"

    def test_long_string_truncated(self):
        from sandcastle.engine.eval import _summarize_output

        long = "x" * 600
        result = _summarize_output(long)
        assert len(result) == 500

    def test_dict_short_unchanged(self):
        from sandcastle.engine.eval import _summarize_output

        d = {"key": "value"}
        assert _summarize_output(d) == d

    def test_dict_long_truncated(self):
        from sandcastle.engine.eval import _summarize_output

        big_dict = {f"key_{i}": "x" * 50 for i in range(30)}
        result = _summarize_output(big_dict)
        assert isinstance(result, str)
        assert len(result) <= 500

    def test_non_string_converted(self):
        from sandcastle.engine.eval import _summarize_output

        result = _summarize_output(12345)
        assert result == "12345"


class TestRunEvalSuite:
    """Cover suite runner - tag filtering, concurrency, exception handling."""

    @pytest.mark.asyncio
    async def test_tag_filter_excludes_unmatched(self):
        from sandcastle.engine.eval import (
            EvalCase,
            EvalSuiteDef,
            AssertionDef,
            run_eval_suite,
        )

        # Patch run_eval_case to avoid real workflow execution
        async def mock_run_case(case, wf_name):
            from sandcastle.engine.eval import CaseResult
            return CaseResult(name=case.name, passed=True)

        suite = EvalSuiteDef(
            workflow="test-wf",
            cases=[
                EvalCase(name="tagged", input={}, tags=["smoke"]),
                EvalCase(name="untagged", input={}, tags=[]),
            ],
        )
        with patch("sandcastle.engine.eval.run_eval_case", side_effect=mock_run_case):
            result = await run_eval_suite(suite, tag_filter=["smoke"])

        assert result.total == 1
        assert result.cases[0].name == "tagged"

    @pytest.mark.asyncio
    async def test_suite_exception_captured(self):
        from sandcastle.engine.eval import EvalCase, EvalSuiteDef, run_eval_suite

        suite = EvalSuiteDef(
            workflow="test-wf",
            cases=[EvalCase(name="error_case", input={})],
        )
        with patch(
            "sandcastle.engine.eval.run_eval_case",
            side_effect=RuntimeError("boom"),
        ):
            result = await run_eval_suite(suite)

        assert result.total == 1
        assert not result.cases[0].passed
        assert "boom" in result.cases[0].error

    @pytest.mark.asyncio
    async def test_suite_pass_rate_calculation(self):
        from sandcastle.engine.eval import (
            CaseResult,
            EvalCase,
            EvalSuiteDef,
            run_eval_suite,
        )

        async def mock_run_case(case, wf_name):
            return CaseResult(name=case.name, passed=case.name == "pass_case")

        suite = EvalSuiteDef(
            workflow="test-wf",
            cases=[
                EvalCase(name="pass_case", input={}),
                EvalCase(name="fail_case", input={}),
            ],
        )
        with patch("sandcastle.engine.eval.run_eval_case", side_effect=mock_run_case):
            result = await run_eval_suite(suite)

        assert result.pass_rate == pytest.approx(0.5)
        assert result.passed == 1
        assert result.failed == 1

    @pytest.mark.asyncio
    async def test_empty_suite_pass_rate_zero(self):
        from sandcastle.engine.eval import EvalSuiteDef, run_eval_suite

        suite = EvalSuiteDef(workflow="test-wf", cases=[])
        result = await run_eval_suite(suite)
        assert result.pass_rate == 0.0
        assert result.total == 0


# ---------------------------------------------------------------------------
# engine/generator.py
# ---------------------------------------------------------------------------


class TestStripFencing:
    """Cover _strip_fencing edge cases."""

    def test_strips_yaml_fence(self):
        from sandcastle.engine.generator import _strip_fencing

        text = "```yaml\nname: my-wf\n```"
        result = _strip_fencing(text)
        assert result == "name: my-wf"

    def test_strips_bare_fence(self):
        from sandcastle.engine.generator import _strip_fencing

        text = "```\nname: my-wf\n```"
        result = _strip_fencing(text)
        assert result == "name: my-wf"

    def test_no_fence_unchanged(self):
        from sandcastle.engine.generator import _strip_fencing

        text = "name: my-wf\ndescription: test"
        result = _strip_fencing(text)
        assert result == text

    def test_strips_yml_fence(self):
        from sandcastle.engine.generator import _strip_fencing

        text = "```yml\nfoo: bar\n```"
        result = _strip_fencing(text)
        assert result == "foo: bar"


class TestProviderConfig:
    """Cover provider config resolution."""

    def test_anthropic_default_provider(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SANDCASTLE_ADVISOR_PROVIDER", None)
            from sandcastle.engine.generator import _get_advisor_config

            config = _get_advisor_config()
        assert config["api_key_env"] == "ANTHROPIC_API_KEY"

    def test_openai_provider(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}):
            from sandcastle.engine.generator import _get_advisor_config

            config = _get_advisor_config()
        assert "openai.com" in config["api_url"]

    def test_ollama_provider(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}):
            from sandcastle.engine.generator import _get_advisor_config

            config = _get_advisor_config()
        assert config["api_key_env"] == ""

    def test_unknown_provider_falls_back_to_anthropic(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "unknown-ai"}):
            from sandcastle.engine.generator import _get_advisor_config

            config = _get_advisor_config()
        assert config["api_key_env"] == "ANTHROPIC_API_KEY"

    def test_model_override_env(self):
        with patch.dict(
            os.environ,
            {
                "SANDCASTLE_ADVISOR_PROVIDER": "anthropic",
                "SANDCASTLE_ADVISOR_MODEL": "claude-haiku-custom",
            },
        ):
            from sandcastle.engine.generator import _get_advisor_config

            config = _get_advisor_config()
        assert config["model"] == "claude-haiku-custom"

    def test_is_anthropic_provider_true(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
            from sandcastle.engine.generator import _is_anthropic_provider

            assert _is_anthropic_provider() is True

    def test_is_anthropic_provider_false_for_openai(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}):
            from sandcastle.engine.generator import _is_anthropic_provider

            assert _is_anthropic_provider() is False

    def test_ollama_resolve_api_key_returns_placeholder(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "ollama"}):
            from sandcastle.engine.generator import _resolve_api_key

            key = _resolve_api_key()
        assert key == "ollama-no-key"


class TestBuildRequestBody:
    """Cover _build_request_body for both providers."""

    def test_anthropic_format(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "anthropic"}):
            from sandcastle.engine.generator import _build_request_body

            body = _build_request_body(
                "claude-sonnet-4",
                "system prompt",
                [{"role": "user", "content": "hi"}],
            )
        assert "system" in body
        assert body["system"] == "system prompt"
        assert body["messages"][0]["role"] == "user"

    def test_openai_format(self):
        with patch.dict(os.environ, {"SANDCASTLE_ADVISOR_PROVIDER": "openai"}):
            from sandcastle.engine.generator import _build_request_body

            body = _build_request_body(
                "gpt-4o",
                "system prompt",
                [{"role": "user", "content": "hi"}],
            )
        # OpenAI format puts system in messages list
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][1]["role"] == "user"


class TestExtractLatestYaml:
    """Cover _extract_latest_yaml chat history scanner."""

    def test_extracts_yaml_from_assistant_message(self):
        import json
        from sandcastle.engine.generator import _extract_latest_yaml

        yaml_content = "name: my-workflow\nsteps: []"
        messages = [
            {"role": "user", "content": "make a workflow"},
            {
                "role": "assistant",
                "content": json.dumps({"mode": "yaml", "yaml": yaml_content}),
            },
        ]
        result = _extract_latest_yaml(messages)
        assert result == yaml_content

    def test_returns_none_when_no_yaml(self):
        from sandcastle.engine.generator import _extract_latest_yaml

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": '{"mode": "questions", "message": "What?"}'},
        ]
        result = _extract_latest_yaml(messages)
        assert result is None

    def test_returns_latest_of_multiple_yamls(self):
        import json
        from sandcastle.engine.generator import _extract_latest_yaml

        messages = [
            {
                "role": "assistant",
                "content": json.dumps({"mode": "yaml", "yaml": "name: first"}),
            },
            {"role": "user", "content": "change it"},
            {
                "role": "assistant",
                "content": json.dumps({"mode": "yaml", "yaml": "name: second"}),
            },
        ]
        result = _extract_latest_yaml(messages)
        assert result == "name: second"

    def test_invalid_json_skipped(self):
        from sandcastle.engine.generator import _extract_latest_yaml

        messages = [
            {"role": "assistant", "content": "not json at all"},
        ]
        result = _extract_latest_yaml(messages)
        assert result is None


class TestExplainErrorFallback:
    """Cover explain_error fallback path when no API key is set."""

    @pytest.mark.asyncio
    async def test_explain_error_no_api_key_returns_fallback(self):
        from sandcastle.engine.generator import explain_error

        with patch("sandcastle.engine.generator._resolve_api_key", return_value=""):
            result = await explain_error(
                "step-1",
                "llm",
                "ConnectionError: timeout",
            )

        assert "summary" in result
        assert "fix" in result
        assert "API key not set" in result["cause"]

    @pytest.mark.asyncio
    async def test_explain_error_http_error_returns_fallback(self):
        import httpx
        from sandcastle.engine.generator import explain_error

        with patch("sandcastle.engine.generator._resolve_api_key", return_value="dummy-key"):
            with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("refused")):
                result = await explain_error("s1", "http", "connection refused")

        assert result["severity"] == "medium"
        assert "AI explanation unavailable" in result["cause"]


class TestScrubSecrets:
    """Cover _scrub_secrets redaction."""

    def test_scrubs_bearer_token(self):
        from sandcastle.engine.generator import _scrub_secrets

        text = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9"
        result = _scrub_secrets(text)
        assert "[REDACTED]" in result
        assert "eyJ" not in result

    def test_scrubs_api_key_assignment(self):
        from sandcastle.engine.generator import _scrub_secrets

        text = 'api_key = "sk-abcdefghij1234567890"'
        result = _scrub_secrets(text)
        assert "sk-abcdef" not in result

    def test_scrubs_pem_key(self):
        from sandcastle.engine.generator import _scrub_secrets

        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----"
        )
        result = _scrub_secrets(pem)
        assert "REDACTED-PEM-KEY" in result

    def test_clean_text_unchanged(self):
        from sandcastle.engine.generator import _scrub_secrets

        text = "The workflow completed successfully with 5 steps."
        result = _scrub_secrets(text)
        assert result == text


# ---------------------------------------------------------------------------
# engine/telemetry.py
# ---------------------------------------------------------------------------


class TestScrubText:
    """Cover _scrub_text sensitive pattern redaction."""

    def test_scrubs_api_key_colon(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "api_key: mysecretvalue123"
        result = _scrub_text(text)
        assert "[REDACTED]" in result

    def test_scrubs_bearer_token(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "bearer abc.def.ghi"
        result = _scrub_text(text)
        assert "[REDACTED]" in result

    def test_scrubs_known_prefix_token(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "token is ghp_abcdefghijklmnopqrstuvwxyz"
        result = _scrub_text(text)
        assert "[REDACTED]" in result

    def test_plain_text_unchanged(self):
        from sandcastle.engine.telemetry import _scrub_text

        text = "Workflow completed. Step count: 5."
        result = _scrub_text(text)
        assert result == text


class TestScrubPath:
    """Cover _scrub_path home directory replacement."""

    def test_home_path_replaced(self):
        from sandcastle.engine.telemetry import _scrub_path

        home = os.path.expanduser("~")
        path = f"{home}/projects/sandcastle/src/main.py"
        result = _scrub_path(path)
        assert result.startswith("~/")

    def test_non_home_path_unchanged(self):
        from sandcastle.engine.telemetry import _scrub_path

        path = "/usr/lib/python3.12/os.py"
        result = _scrub_path(path)
        assert result == path


class TestAnonymizeEvent:
    """Cover _anonymize_event - the sentry before_send hook."""

    def test_removes_request_and_user(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {
            "request": {"url": "http://example.com", "headers": {"X-API-Key": "secret"}},
            "user": {"ip_address": "1.2.3.4"},
            "message": "hello",
        }
        with patch("sandcastle.engine.telemetry._save_local_report"):
            result = _anonymize_event(event, {})
        assert "request" not in result
        assert "user" not in result

    def test_scrubs_exception_value(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {
            "exception": {
                "values": [
                    {"value": "Error: api_key: secretvalue12345", "stacktrace": {"frames": []}}
                ]
            }
        }
        with patch("sandcastle.engine.telemetry._save_local_report"):
            result = _anonymize_event(event, {})
        exc_val = result["exception"]["values"][0]["value"]
        assert "secretvalue12345" not in exc_val

    def test_scrubs_frame_abs_path(self):
        from sandcastle.engine.telemetry import _anonymize_event

        home = os.path.expanduser("~")
        event = {
            "exception": {
                "values": [
                    {
                        "value": "error",
                        "stacktrace": {
                            "frames": [{"abs_path": f"{home}/myproject/file.py", "vars": {"k": "v"}}]
                        },
                    }
                ]
            }
        }
        with patch("sandcastle.engine.telemetry._save_local_report"):
            result = _anonymize_event(event, {})
        frame = result["exception"]["values"][0]["stacktrace"]["frames"][0]
        assert frame["abs_path"].startswith("~/")
        assert "vars" not in frame

    def test_scrubs_breadcrumb_message(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {
            "breadcrumbs": {
                "values": [
                    {"message": "token: xoxb-supersecret", "data": {"url": "http://test.com"}}
                ]
            }
        }
        with patch("sandcastle.engine.telemetry._save_local_report"):
            result = _anonymize_event(event, {})
        crumb = result["breadcrumbs"]["values"][0]
        assert "xoxb-supersecret" not in crumb["message"]

    def test_scrubs_logentry_formatted(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {
            "logentry": {"formatted": "Error: password: topsecret123"},
        }
        with patch("sandcastle.engine.telemetry._save_local_report"):
            result = _anonymize_event(event, {})
        assert "topsecret123" not in result["logentry"]["formatted"]

    def test_scrubs_message_string(self):
        from sandcastle.engine.telemetry import _anonymize_event

        event = {"message": "api_key=supersecretvalue12345"}
        with patch("sandcastle.engine.telemetry._save_local_report"):
            result = _anonymize_event(event, {})
        assert "supersecretvalue12345" not in result["message"]


class TestTelemetryInit:
    """Cover init_sentry / is_enabled / _reset."""

    def test_is_enabled_false_initially(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        assert not telemetry.is_enabled()

    def test_init_sentry_disabled_when_no_dsn(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.sentry_dsn = ""
            result = telemetry.init_sentry()
        assert result is False

    def test_init_sentry_disabled_when_flag_false(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.telemetry_enabled = False
            mock_settings.sentry_dsn = "https://abc@sentry.io/1"
            result = telemetry.init_sentry()
        assert result is False

    def test_set_workflow_context_noop_when_not_initialized(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        # Should not raise even though sentry is not initialized
        telemetry.set_workflow_context("test-workflow", "run-123")

    def test_capture_step_error_noop_when_not_initialized(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        # Should not raise
        telemetry.capture_step_error(
            ValueError("test"),
            step_id="s1",
            step_type="llm",
        )

    def test_capture_backend_error_noop_when_not_initialized(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        # Should not raise
        telemetry.capture_backend_error(
            RuntimeError("backend down"),
            backend="e2b",
            operation="run",
        )

    def test_init_sentry_import_error_returns_false(self):
        from sandcastle.engine import telemetry
        import sys

        telemetry._reset()
        # Remove sentry_sdk from modules to force ImportError
        orig = sys.modules.pop("sentry_sdk", None)
        orig_fastapi = sys.modules.pop("sentry_sdk.integrations.fastapi", None)
        orig_logging = sys.modules.pop("sentry_sdk.integrations.logging", None)
        try:
            with patch("sandcastle.config.settings") as mock_settings:
                mock_settings.telemetry_enabled = True
                mock_settings.sentry_dsn = "https://abc@sentry.io/1"
                with patch("builtins.__import__", side_effect=ImportError("no sentry")):
                    result = telemetry.init_sentry()
        except Exception:
            result = False
        finally:
            if orig is not None:
                sys.modules["sentry_sdk"] = orig
            if orig_fastapi is not None:
                sys.modules["sentry_sdk.integrations.fastapi"] = orig_fastapi
            if orig_logging is not None:
                sys.modules["sentry_sdk.integrations.logging"] = orig_logging
        assert result is False

    def test_init_sentry_exception_returns_false(self):
        from sandcastle.engine import telemetry

        telemetry._reset()
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.telemetry_enabled = True
            mock_settings.sentry_dsn = "https://abc@sentry.io/1"
            mock_sentry = MagicMock()
            mock_sentry.init = MagicMock(side_effect=Exception("sentry error"))
            mock_sentry.integrations = MagicMock()
            mock_sentry.integrations.fastapi = MagicMock()
            mock_sentry.integrations.logging = MagicMock()
            mock_sentry.integrations.fastapi.FastApiIntegration = MagicMock()
            mock_sentry.integrations.logging.LoggingIntegration = MagicMock()
            with patch.dict("sys.modules", {
                "sentry_sdk": mock_sentry,
                "sentry_sdk.integrations.fastapi": mock_sentry.integrations.fastapi,
                "sentry_sdk.integrations.logging": mock_sentry.integrations.logging,
            }):
                result = telemetry.init_sentry()
        assert result is False


class TestSaveLocalReport:
    """Cover _save_local_report."""

    def test_saves_json_file(self, tmp_path):
        from sandcastle.engine.telemetry import _save_local_report

        event = {
            "event_id": "abc123",
            "level": "error",
            "exception": {
                "values": [{"type": "ValueError", "value": "test error"}]
            },
        }
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.data_dir = str(tmp_path)
            _save_local_report(event)

        reports = list((tmp_path / "error_reports").glob("*.json"))
        assert len(reports) == 1
        import json
        data = json.loads(reports[0].read_text())
        assert data["event_id"] == "abc123"
        assert data["exceptions"][0]["type"] == "ValueError"

    def test_saves_report_without_exception(self, tmp_path):
        from sandcastle.engine.telemetry import _save_local_report

        event = {
            "event_id": "xyz789",
            "level": "warning",
            "tags": {"workflow": "test-wf"},
        }
        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.data_dir = str(tmp_path)
            _save_local_report(event)

        reports = list((tmp_path / "error_reports").glob("*.json"))
        assert len(reports) == 1

    def test_save_report_swallows_exceptions(self, tmp_path):
        """_save_local_report must not raise even on filesystem errors."""
        from sandcastle.engine.telemetry import _save_local_report

        with patch("sandcastle.config.settings") as mock_settings:
            mock_settings.data_dir = str(tmp_path)
            with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
                # Should not raise
                _save_local_report({"event_id": "err123"})


# ---------------------------------------------------------------------------
# api/a2a.py
# ---------------------------------------------------------------------------


class TestAgentCardBuilder:
    """Cover _build_agent_card function."""

    def test_agent_card_structure(self):
        from sandcastle.api.a2a import _build_agent_card

        card = _build_agent_card("https://example.com")
        assert card["name"] == "Sandcastle"
        assert card["url"] == "https://example.com"
        assert "skills" in card
        assert len(card["skills"]) == 2
        assert card["capabilities"]["streaming"] is True

    def test_agent_card_auth_not_required(self):
        """A2A v1.0: auth-not-required -> empty `security` array."""
        from sandcastle.api.a2a import _build_agent_card

        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.auth_required = False
            card = _build_agent_card("https://example.com")
        assert card["security"] == []
        assert card["securitySchemes"]["apiKey"]["type"] == "apiKey"

    def test_agent_card_auth_required(self):
        """A2A v1.0: auth-required -> non-empty `security` array."""
        from sandcastle.api.a2a import _build_agent_card

        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.auth_required = True
            card = _build_agent_card("https://example.com")
        assert card["security"] == [{"apiKey": []}]
        assert card["securitySchemes"]["apiKey"]["type"] == "apiKey"


class TestJsonRpcHelpers:
    """Cover _jsonrpc_error and _jsonrpc_result helpers."""

    def test_jsonrpc_error_with_data(self):
        from sandcastle.api.a2a import _jsonrpc_error

        response = _jsonrpc_error("req-1", -32600, "Bad request", data={"detail": "x"})
        body = response.body
        import json
        parsed = json.loads(body)
        assert parsed["error"]["code"] == -32600
        assert parsed["error"]["data"]["detail"] == "x"
        assert parsed["id"] == "req-1"

    def test_jsonrpc_error_without_data(self):
        from sandcastle.api.a2a import _jsonrpc_error

        import json
        response = _jsonrpc_error(None, -32700, "Parse error")
        parsed = json.loads(response.body)
        assert "data" not in parsed["error"]

    def test_jsonrpc_result(self):
        from sandcastle.api.a2a import _jsonrpc_result

        import json
        response = _jsonrpc_result("id-42", {"status": "ok"})
        parsed = json.loads(response.body)
        assert parsed["result"]["status"] == "ok"
        assert parsed["id"] == "id-42"


class TestMapStatus:
    """Cover _map_status function."""

    def test_known_statuses(self):
        from sandcastle.api.a2a import _map_status

        assert _map_status("queued") == "submitted"
        assert _map_status("running") == "working"
        assert _map_status("completed") == "completed"
        assert _map_status("failed") == "failed"
        assert _map_status("cancelled") == "canceled"
        assert _map_status("awaiting_approval") == "input-required"
        assert _map_status("budget_exceeded") == "failed"
        assert _map_status("partial") == "completed"

    def test_unknown_status(self):
        from sandcastle.api.a2a import _map_status

        assert _map_status("mystery_status") == "unknown"


class TestExtractWorkflowName:
    """Cover _extract_workflow_name."""

    def test_extracts_from_data_part(self):
        from sandcastle.api.a2a import _extract_workflow_name

        msg = {"parts": [{"type": "data", "data": {"workflow_name": "my-flow"}}]}
        assert _extract_workflow_name(msg) == "my-flow"

    def test_extracts_from_text_part(self):
        from sandcastle.api.a2a import _extract_workflow_name

        msg = {"parts": [{"type": "text", "text": "my-flow"}]}
        assert _extract_workflow_name(msg) == "my-flow"

    def test_returns_none_for_empty_parts(self):
        from sandcastle.api.a2a import _extract_workflow_name

        assert _extract_workflow_name({"parts": []}) is None

    def test_returns_none_for_non_dict(self):
        from sandcastle.api.a2a import _extract_workflow_name

        assert _extract_workflow_name("not a dict") is None

    def test_returns_none_for_non_list_parts(self):
        from sandcastle.api.a2a import _extract_workflow_name

        assert _extract_workflow_name({"parts": "invalid"}) is None


class TestExtractInput:
    """Cover _extract_input."""

    def test_extracts_input_from_data_part(self):
        from sandcastle.api.a2a import _extract_input

        msg = {
            "parts": [
                {
                    "type": "data",
                    "data": {"workflow_name": "wf", "input": {"key": "value"}},
                }
            ]
        }
        result = _extract_input(msg)
        assert result == {"key": "value"}

    def test_returns_empty_dict_when_no_input(self):
        from sandcastle.api.a2a import _extract_input

        msg = {"parts": [{"type": "text", "text": "hello"}]}
        result = _extract_input(msg)
        assert result == {}

    def test_returns_empty_dict_for_non_dict(self):
        from sandcastle.api.a2a import _extract_input

        assert _extract_input("invalid") == {}


class TestBuildTaskResponse:
    """Cover _build_task_response."""

    def test_basic_task(self):
        from sandcastle.api.a2a import _build_task_response

        task = _build_task_response("task-1", "working")
        assert task["id"] == "task-1"
        assert task["status"]["state"] == "working"
        assert "artifacts" not in task

    def test_task_with_output(self):
        from sandcastle.api.a2a import _build_task_response

        task = _build_task_response("t1", "completed", output_data={"result": "ok"})
        assert task["artifacts"][0]["parts"][0]["data"]["result"] == "ok"

    def test_task_with_error(self):
        from sandcastle.api.a2a import _build_task_response

        task = _build_task_response("t1", "failed", error="Something went wrong")
        assert task["status"]["message"] == "Something went wrong"


class TestHandleTasksSend:
    """Cover _handle_tasks_send validation paths."""

    @pytest.mark.asyncio
    async def test_invalid_task_id_too_long(self):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {"id": "x" * 300, "message": {}}
        result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"
        assert "Invalid task id" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_unknown_skill_id(self):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {"id": "task-1", "skillId": "unknown-skill", "message": {}}
        result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"
        assert "Unknown skill" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_list_workflows_skill(self):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {"id": "task-1", "skillId": "list-workflows", "message": {}}
        with patch(
            "sandcastle.api.a2a._list_available_workflows", return_value=[]
        ):
            result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "completed"
        assert "workflows" in result["artifacts"][0]["parts"][0]["data"]

    @pytest.mark.asyncio
    async def test_missing_workflow_name(self):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {"id": "task-1", "message": {"parts": []}}
        result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"
        assert "workflow_name" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_workflow_name_too_long(self):
        from sandcastle.api.a2a import _handle_tasks_send

        long_name = "x" * 300
        params = {
            "id": "task-1",
            "message": {"parts": [{"type": "text", "text": long_name}]},
        }
        result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"
        assert "too long" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_path_traversal_in_workflow_name(self):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {
            "id": "task-1",
            "message": {"parts": [{"type": "text", "text": "../etc/passwd"}]},
        }
        result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"
        assert "Invalid workflow name" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_control_char_in_workflow_name(self):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {
            "id": "task-1",
            "message": {"parts": [{"type": "text", "text": "bad\x00name"}]},
        }
        result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"

    @pytest.mark.asyncio
    async def test_workflow_not_found(self, tmp_path):
        from sandcastle.api.a2a import _handle_tasks_send

        params = {
            "id": "task-1",
            "message": {"parts": [{"type": "text", "text": "nonexistent-workflow"}]},
        }
        with patch("sandcastle.api.a2a.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            mock_settings.auth_required = False
            result = await _handle_tasks_send(params)
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]


class TestHandleTasksGet:
    """Cover _handle_tasks_get validation."""

    @pytest.mark.asyncio
    async def test_missing_task_id(self):
        from sandcastle.api.a2a import _handle_tasks_get

        result = await _handle_tasks_get({})
        assert result["status"]["state"] == "failed"
        assert "Missing task id" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_uuid_format(self):
        from sandcastle.api.a2a import _handle_tasks_get

        result = await _handle_tasks_get({"id": "not-a-uuid"})
        assert result["status"]["state"] == "failed"
        assert "Invalid task id format" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_task_not_found(self):
        from sandcastle.api.a2a import _handle_tasks_get
        import uuid

        task_id = str(uuid.uuid4())
        result = await _handle_tasks_get({"id": task_id})
        assert result["status"]["state"] == "failed"
        assert "not found" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_task_id_too_long(self):
        from sandcastle.api.a2a import _handle_tasks_get

        result = await _handle_tasks_get({"id": "x" * 300})
        assert result["status"]["state"] == "failed"


class TestHandleTasksCancel:
    """Cover _handle_tasks_cancel validation."""

    @pytest.mark.asyncio
    async def test_missing_task_id(self):
        from sandcastle.api.a2a import _handle_tasks_cancel

        result = await _handle_tasks_cancel({})
        assert result["status"]["state"] == "failed"

    @pytest.mark.asyncio
    async def test_invalid_uuid_format(self):
        from sandcastle.api.a2a import _handle_tasks_cancel

        result = await _handle_tasks_cancel({"id": "not-a-uuid"})
        assert result["status"]["state"] == "failed"
        assert "Invalid task id format" in result["status"]["message"]

    @pytest.mark.asyncio
    async def test_task_not_found(self):
        from sandcastle.api.a2a import _handle_tasks_cancel
        import uuid

        task_id = str(uuid.uuid4())
        result = await _handle_tasks_cancel({"id": task_id})
        assert result["status"]["state"] == "failed"

    @pytest.mark.asyncio
    async def test_task_id_too_long(self):
        from sandcastle.api.a2a import _handle_tasks_cancel

        result = await _handle_tasks_cancel({"id": "x" * 300})
        assert result["status"]["state"] == "failed"


class TestGetTenantIdSafe:
    """Cover _get_tenant_id_safe."""

    def test_returns_tenant_id_when_present(self):
        from sandcastle.api.a2a import _get_tenant_id_safe

        request = MagicMock()
        request.state.tenant_id = "tenant-abc"
        assert _get_tenant_id_safe(request) == "tenant-abc"

    def test_returns_none_when_missing(self):
        from sandcastle.api.a2a import _get_tenant_id_safe

        request = MagicMock(spec=[])
        request.state = MagicMock(spec=[])
        assert _get_tenant_id_safe(request) is None


# ---------------------------------------------------------------------------
# queue/scheduler.py
# ---------------------------------------------------------------------------


class TestSchedulerGetCreate:
    """Cover get_scheduler - singleton creation."""

    def test_get_scheduler_returns_instance(self):
        from sandcastle.queue.scheduler import get_scheduler

        scheduler = get_scheduler()
        assert scheduler is not None

    def test_get_scheduler_singleton(self):
        from sandcastle.queue.scheduler import get_scheduler

        s1 = get_scheduler()
        s2 = get_scheduler()
        assert s1 is s2


class TestAddSchedule:
    """Cover add_schedule - valid, invalid cron, empty inputs."""

    def test_add_schedule_empty_cron_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="cron_expression"):
            add_schedule("s1", "", "my-workflow")

    def test_add_schedule_whitespace_cron_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="cron_expression"):
            add_schedule("s1", "   ", "my-workflow")

    def test_add_schedule_empty_workflow_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="workflow_name"):
            add_schedule("s1", "* * * * *", "")

    def test_add_schedule_invalid_cron_raises(self):
        from sandcastle.queue.scheduler import add_schedule

        with pytest.raises(ValueError, match="Invalid cron expression"):
            add_schedule("s1", "not a cron", "my-workflow")

    def test_add_schedule_workflow_not_found_raises(self, tmp_path):
        from sandcastle.queue.scheduler import add_schedule

        with patch("sandcastle.queue.scheduler.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            with pytest.raises(ValueError, match="not found"):
                add_schedule("s1", "* * * * *", "nonexistent-workflow")

    def test_add_schedule_skips_validation_when_dir_missing(self):
        from sandcastle.queue.scheduler import add_schedule

        with patch("sandcastle.queue.scheduler.settings") as mock_settings:
            mock_settings.workflows_dir = "/nonexistent/dir"
            scheduler = MagicMock()
            scheduler.add_job = MagicMock()
            with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
                # Should not raise - dir doesn't exist so workflow check is skipped
                add_schedule("s1", "* * * * *", "any-workflow")
                scheduler.add_job.assert_called_once()

    def test_add_schedule_with_existing_workflow(self, tmp_path):
        from sandcastle.queue.scheduler import add_schedule

        # Create a fake workflow file
        (tmp_path / "my-workflow.yaml").write_text("name: my-workflow\nsteps: []")

        with patch("sandcastle.queue.scheduler.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            scheduler = MagicMock()
            scheduler.add_job = MagicMock()
            with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
                add_schedule("s1", "0 * * * *", "my-workflow")
                scheduler.add_job.assert_called_once()


class TestRemoveSchedule:
    """Cover remove_schedule."""

    def test_remove_existing_schedule_returns_true(self):
        from sandcastle.queue.scheduler import remove_schedule

        scheduler = MagicMock()
        scheduler.remove_job = MagicMock()
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            result = remove_schedule("s1")
        assert result is True

    def test_remove_nonexistent_schedule_returns_false(self):
        from sandcastle.queue.scheduler import remove_schedule

        scheduler = MagicMock()
        scheduler.remove_job = MagicMock(side_effect=Exception("not found"))
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            result = remove_schedule("nonexistent")
        assert result is False


class TestListSchedules:
    """Cover list_schedules."""

    def test_list_returns_job_dicts(self):
        from sandcastle.queue.scheduler import list_schedules

        job = MagicMock()
        job.id = "sched-1"
        job.name = "Test Schedule"
        job.next_run_time = None
        job.trigger = MagicMock()
        job.trigger.__str__ = lambda self: "cron[* * * * *]"

        scheduler = MagicMock()
        scheduler.get_jobs.return_value = [job]
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            jobs = list_schedules()

        assert len(jobs) == 1
        assert jobs[0]["id"] == "sched-1"
        assert jobs[0]["name"] == "Test Schedule"
        assert jobs[0]["next_run_time"] is None

    def test_list_empty_returns_empty_list(self):
        from sandcastle.queue.scheduler import list_schedules

        scheduler = MagicMock()
        scheduler.get_jobs.return_value = []
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            jobs = list_schedules()
        assert jobs == []


class TestStartStopScheduler:
    """Cover start_scheduler and stop_scheduler."""

    @pytest.mark.asyncio
    async def test_start_scheduler_starts_if_not_running(self):
        from sandcastle.queue.scheduler import start_scheduler

        scheduler = MagicMock()
        scheduler.running = False
        scheduler.start = MagicMock()
        scheduler.add_job = MagicMock()
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            await start_scheduler()
        scheduler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_scheduler_noop_if_already_running(self):
        from sandcastle.queue.scheduler import start_scheduler

        scheduler = MagicMock()
        scheduler.running = True
        scheduler.start = MagicMock()
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            await start_scheduler()
        scheduler.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_scheduler_stops_if_running(self):
        from sandcastle.queue.scheduler import stop_scheduler

        scheduler = MagicMock()
        scheduler.running = True
        scheduler.shutdown = MagicMock()
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            await stop_scheduler()
        scheduler.shutdown.assert_called_once_with(wait=False)

    @pytest.mark.asyncio
    async def test_stop_scheduler_noop_if_not_running(self):
        from sandcastle.queue.scheduler import stop_scheduler

        scheduler = MagicMock()
        scheduler.running = False
        scheduler.shutdown = MagicMock()
        with patch("sandcastle.queue.scheduler.get_scheduler", return_value=scheduler):
            await stop_scheduler()
        scheduler.shutdown.assert_not_called()


class TestLoadWorkflowYaml:
    """Cover _load_workflow_yaml."""

    def test_loads_yaml_file(self, tmp_path):
        from sandcastle.queue.scheduler import _load_workflow_yaml

        (tmp_path / "my-wf.yaml").write_text("name: my-wf")
        with patch("sandcastle.queue.scheduler.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            content = _load_workflow_yaml("my-wf")
        assert content == "name: my-wf"

    def test_file_not_found_raises(self, tmp_path):
        from sandcastle.queue.scheduler import _load_workflow_yaml

        with patch("sandcastle.queue.scheduler.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            with pytest.raises(FileNotFoundError, match="not found"):
                _load_workflow_yaml("nonexistent")

    def test_path_traversal_raises(self, tmp_path):
        from sandcastle.queue.scheduler import _load_workflow_yaml

        with patch("sandcastle.queue.scheduler.settings") as mock_settings:
            mock_settings.workflows_dir = str(tmp_path)
            with pytest.raises(ValueError, match="traversal"):
                _load_workflow_yaml("../../etc/passwd")


# ---------------------------------------------------------------------------
# engine/pdf.py
# ---------------------------------------------------------------------------


class TestStripInlineMd:
    """Cover _strip_inline_md."""

    def test_strips_bold(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("**bold text**") == "bold text"

    def test_strips_italic(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("*italic*") == "italic"

    def test_strips_code(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("`code`") == "code"

    def test_strips_link(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("[text](http://example.com)") == "text"

    def test_strips_strikethrough(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("~~strike~~") == "strike"

    def test_strips_html_br(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("line1<br/>line2") == "line1 line2"

    def test_collapses_spaces(self):
        from sandcastle.engine.pdf import _strip_inline_md

        assert _strip_inline_md("a  b   c") == "a b c"

    def test_truncates_very_long_input(self):
        from sandcastle.engine.pdf import _strip_inline_md

        long = "x" * 15000
        result = _strip_inline_md(long)
        assert len(result) <= 10_000


class TestExtractNumeric:
    """Cover _extract_numeric."""

    def test_integer(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("42") == 42.0

    def test_float(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("3.14") == pytest.approx(3.14)

    def test_currency(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("$1,234") == pytest.approx(1234.0)

    def test_percentage(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("85%") == pytest.approx(85.0)

    def test_empty_returns_none(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("") is None

    def test_text_returns_none(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("hello") is None

    def test_infinity_returns_none(self):
        from sandcastle.engine.pdf import _extract_numeric

        assert _extract_numeric("inf") is None


class TestExtractSeverityScore:
    """Cover _extract_severity_score."""

    def test_critical(self):
        from sandcastle.engine.pdf import _extract_severity_score

        assert _extract_severity_score("critical") == 4

    def test_high(self):
        from sandcastle.engine.pdf import _extract_severity_score

        assert _extract_severity_score("high") == 3

    def test_medium(self):
        from sandcastle.engine.pdf import _extract_severity_score

        assert _extract_severity_score("medium") == 2

    def test_low(self):
        from sandcastle.engine.pdf import _extract_severity_score

        assert _extract_severity_score("low") == 1

    def test_p0(self):
        from sandcastle.engine.pdf import _extract_severity_score

        assert _extract_severity_score("P0") == 4

    def test_unknown_returns_none(self):
        from sandcastle.engine.pdf import _extract_severity_score

        assert _extract_severity_score("irrelevant text") is None


class TestParseKpiMarkers:
    """Cover _parse_kpi_markers."""

    def test_single_kpi(self):
        from sandcastle.engine.pdf import _parse_kpi_markers

        result = _parse_kpi_markers("<!-- kpi: Revenue=$2.4M(+12%) -->")
        assert result is not None
        assert len(result) == 1
        label, value, change, positive = result[0]
        assert label == "Revenue"
        assert value == "$2.4M"
        assert change == "+12%"
        assert positive is True

    def test_multiple_kpis(self):
        from sandcastle.engine.pdf import _parse_kpi_markers

        result = _parse_kpi_markers("<!-- kpi: A=10(+5)|B=20(-3) -->")
        assert result is not None
        assert len(result) == 2

    def test_negative_change(self):
        from sandcastle.engine.pdf import _parse_kpi_markers

        result = _parse_kpi_markers("<!-- kpi: Churn=5%(-0.3%) -->")
        assert result is not None
        _, _, change, positive = result[0]
        assert positive is False

    def test_no_kpi_marker_returns_none(self):
        from sandcastle.engine.pdf import _parse_kpi_markers

        assert _parse_kpi_markers("regular text") is None


class TestDetectCallout:
    """Cover _detect_callout."""

    def test_detects_important(self):
        from sandcastle.engine.pdf import _detect_callout

        result = _detect_callout("> [!IMPORTANT] Read this carefully")
        assert result is not None
        level, text = result
        assert level == "important"

    def test_detects_warning(self):
        from sandcastle.engine.pdf import _detect_callout

        result = _detect_callout("> [!WARNING] Be careful")
        assert result is not None
        assert result[0] == "warning"

    def test_detects_note(self):
        from sandcastle.engine.pdf import _detect_callout

        result = _detect_callout("> [!NOTE] Just a note")
        assert result is not None
        assert result[0] == "note"

    def test_non_callout_returns_none(self):
        from sandcastle.engine.pdf import _detect_callout

        assert _detect_callout("> plain quote") is None
        assert _detect_callout("not a quote") is None


class TestGenerateBrandedPdf:
    """Cover generate_branded_pdf main function."""

    def test_type_error_for_non_string(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        with pytest.raises(TypeError, match="string"):
            generate_branded_pdf(12345, tmp_path / "out.pdf")

    def test_value_error_for_empty_path(self):
        from sandcastle.engine.pdf import generate_branded_pdf

        with pytest.raises(ValueError, match="output_path"):
            generate_branded_pdf("# Hello", "")

    def test_generates_pdf_for_simple_markdown(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        out = tmp_path / "report.pdf"
        result = generate_branded_pdf("# Hello\n\nThis is a test.", str(out))
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_truncates_very_large_input(self, tmp_path):
        from sandcastle.engine.pdf import _MAX_MARKDOWN_SIZE, generate_branded_pdf

        big = "x " * ((_MAX_MARKDOWN_SIZE // 2) + 100)
        out = tmp_path / "big.pdf"
        result = generate_branded_pdf(big, str(out))
        assert result.exists()

    def test_cleans_up_on_failure(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        out = tmp_path / "fail.pdf"
        with patch("sandcastle.engine.pdf._render_markdown", side_effect=RuntimeError("crash")):
            with pytest.raises(RuntimeError, match="crash"):
                generate_branded_pdf("# Hello", str(out))
        # File should be cleaned up
        assert not out.exists()

    def test_renders_headers(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "# Title\n## Section\n### Subsection\n#### Detail\n"
        out = tmp_path / "headers.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_table(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "| Name | Score |\n|------|-------|\n| Alice | 95 |\n| Bob | 87 |"
        out = tmp_path / "table.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_code_block(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "```python\nprint('hello')\n```"
        out = tmp_path / "code.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_horizontal_rule(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "Before\n\n---\n\nAfter"
        out = tmp_path / "hr.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_callout(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "> [!WARNING] This is a warning\n\nsome text"
        out = tmp_path / "callout.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_blockquote(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "> A plain blockquote without callout marker"
        out = tmp_path / "blockquote.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_bullet_list(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "- Item one\n- Item two\n  - Nested item"
        out = tmp_path / "bullets.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_numbered_list(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "1. First item\n2. Second item\n3. Third item"
        out = tmp_path / "numbered.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_kpi_tiles(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "<!-- kpi: Revenue=$1M(+10%)|NPS=72(+5) -->\n\nsome text"
        out = tmp_path / "kpi.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_executive_summary(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        md = "## Executive Summary\n\n- Key finding one\n- Key finding two\n\n## Next Section\n"
        out = tmp_path / "exec.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_renders_multi_page(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        # Enough content to trigger page breaks
        md = "\n".join([f"## Section {i}\n\n" + ("Text content. " * 30) for i in range(20)])
        out = tmp_path / "multipage.pdf"
        result = generate_branded_pdf(md, str(out))
        assert result.exists()

    def test_creates_output_directory(self, tmp_path):
        from sandcastle.engine.pdf import generate_branded_pdf

        out = tmp_path / "nested" / "deep" / "report.pdf"
        result = generate_branded_pdf("# Hello", str(out))
        assert result.exists()
