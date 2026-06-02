"""Tests for DGX Spark hardware detection, Spark Mode config, and API exposure.

Detection is fail-closed; all hardware is mocked so these run on any machine. We
patch at the module's own seams (sandcastle.engine.spark.*) and clear the detection
cache between cases so it never leaks across tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

import sandcastle.engine.spark as spark
from sandcastle.api.schemas import RuntimeInfoResponse
from sandcastle.config import Settings
from sandcastle.engine.spark import SparkInfo, detect_spark, get_spark_info


@pytest.fixture(autouse=True)
def _clear_spark_cache():
    """Reset the module-level detection cache before and after each test."""
    spark._CACHED_SPARK_INFO = None
    yield
    spark._CACHED_SPARK_INFO = None


def _linux():
    """Patch os so the module believes it is on Linux."""
    return patch.multiple(
        "sandcastle.engine.spark.os",
        name="posix",
        uname=MagicMock(return_value=MagicMock(sysname="Linux")),
    )


# ---- detect_spark: fail-closed paths ---------------------------------------


def test_not_spark_on_non_linux():
    with patch.multiple(
        "sandcastle.engine.spark.os",
        name="posix",
        uname=MagicMock(return_value=MagicMock(sysname="Darwin")),
    ):
        assert detect_spark().is_spark is False


def test_not_spark_when_nvidia_smi_missing():
    with _linux(), patch("sandcastle.engine.spark._nvidia_query", return_value=None):
        assert detect_spark().is_spark is False


def test_not_spark_on_non_blackwell_gpu():
    with _linux(), patch(
        "sandcastle.engine.spark._nvidia_query", return_value="NVIDIA GeForce RTX 4090"
    ), patch("sandcastle.engine.spark._system_mem_gb", return_value=128):
        assert detect_spark().is_spark is False


def test_not_spark_on_insufficient_memory():
    with _linux(), patch(
        "sandcastle.engine.spark._nvidia_query", return_value="NVIDIA GB10"
    ), patch("sandcastle.engine.spark._system_mem_gb", return_value=64):
        assert detect_spark().is_spark is False


# ---- detect_spark: success --------------------------------------------------


def test_detect_spark_gb10():
    def fake_query(fields: str) -> str:
        return "NVIDIA GB10" if fields == "name" else "550.40.07"

    with _linux(), patch(
        "sandcastle.engine.spark._nvidia_query", side_effect=fake_query
    ), patch("sandcastle.engine.spark._system_mem_gb", return_value=128):
        info = detect_spark()
        assert info.is_spark is True
        assert info.gpu_name == "NVIDIA GB10"
        assert info.unified_mem_gb == 128
        assert info.cuda == "550.40.07"


# ---- helper parsing ---------------------------------------------------------


def test_nvidia_query_parses_first_row():
    completed = MagicMock(returncode=0, stdout="NVIDIA GB10\n")
    with patch("sandcastle.engine.spark.subprocess.run", return_value=completed):
        assert spark._nvidia_query("name") == "NVIDIA GB10"


def test_nvidia_query_none_when_binary_absent():
    with patch("sandcastle.engine.spark.subprocess.run", side_effect=FileNotFoundError):
        assert spark._nvidia_query("name") is None


def test_system_mem_gb_parses_meminfo():
    meminfo = "MemTotal:       134217728 kB\nMemFree: 1000 kB\n"
    with patch("sandcastle.engine.spark.open", mock_open(read_data=meminfo)):
        assert spark._system_mem_gb() == 128  # 134217728 kB / 1024 / 1024


# ---- caching ----------------------------------------------------------------


def test_get_spark_info_caches():
    with patch(
        "sandcastle.engine.spark.detect_spark", return_value=SparkInfo(is_spark=True)
    ) as m:
        first = get_spark_info()
        second = get_spark_info()
        assert m.call_count == 1
        assert first is second


# ---- config: spark_mode + auto-config --------------------------------------


def test_spark_mode_false_by_default(monkeypatch):
    monkeypatch.delenv("SANDCASTLE_SPARK_MODE", raising=False)
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=False)):
        assert Settings().spark_mode is False


def test_spark_mode_auto_true(monkeypatch):
    monkeypatch.delenv("SANDCASTLE_SPARK_MODE", raising=False)
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=True)):
        assert Settings().spark_mode is True


def test_spark_mode_override_on(monkeypatch):
    monkeypatch.setenv("SANDCASTLE_SPARK_MODE", "on")
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=False)):
        assert Settings().spark_mode is True


def test_spark_mode_override_off(monkeypatch):
    monkeypatch.setenv("SANDCASTLE_SPARK_MODE", "off")
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=True)):
        assert Settings().spark_mode is False


def test_max_concurrent_auto_bump_on_spark(monkeypatch):
    monkeypatch.delenv("MAX_CONCURRENT_SANDBOXES", raising=False)
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=True)):
        assert Settings().max_concurrent_sandboxes == 40


def test_max_concurrent_user_value_respected(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_SANDBOXES", "12")
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=True)):
        assert Settings().max_concurrent_sandboxes == 12


def test_max_concurrent_default_off_spark(monkeypatch):
    monkeypatch.delenv("MAX_CONCURRENT_SANDBOXES", raising=False)
    with patch("sandcastle.config.get_spark_info", return_value=SparkInfo(is_spark=False)):
        assert Settings().max_concurrent_sandboxes == 5


# ---- API schema -------------------------------------------------------------


def test_runtime_response_exposes_spark_mode():
    resp = RuntimeInfoResponse(
        mode="local", database="sqlite", queue="in-process", storage="local", spark_mode=True
    )
    assert resp.spark_mode is True
    # defaults False when omitted
    assert (
        RuntimeInfoResponse(
            mode="local", database="sqlite", queue="in-process", storage="local"
        ).spark_mode
        is False
    )
