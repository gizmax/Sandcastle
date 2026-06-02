"""DGX Spark hardware detection for "Spark Mode" auto-configuration.

Detection is FAIL-CLOSED: a non-Linux host, a missing/erroring ``nvidia-smi``, a
non-Blackwell GPU, or insufficient unified memory all yield
``SparkInfo(is_spark=False)`` so nothing changes off a Spark. The result is cached
for the process lifetime because it is consulted at config-load time.

NOTE: the exact ``nvidia-smi`` product string for a DGX Spark (GB10 Grace-Blackwell)
should be confirmed on real hardware; add any additional identifier to
``_SPARK_GPU_MARKERS``. ``SANDCASTLE_SPARK_MODE=on|off`` always overrides detection.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

# Lowercased substrings that identify a DGX Spark / Grace-Blackwell class GPU.
_SPARK_GPU_MARKERS = ("gb10", "spark", "blackwell")
# A DGX Spark ships ~128GB unified memory; require a high floor so an ordinary
# GPU box (e.g. a 24GB consumer card + plenty of RAM) never matches on memory.
_MIN_UNIFIED_MEM_GB = 110


@dataclass(frozen=True)
class SparkInfo:
    """Result of a Spark hardware probe."""

    is_spark: bool
    gpu_name: str | None = None
    unified_mem_gb: int | None = None
    cuda: str | None = None


def _nvidia_query(fields: str) -> str | None:
    """Run ``nvidia-smi --query-gpu=<fields>`` and return the first row, or None."""
    try:
        result = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    rows = result.stdout.strip().splitlines()
    return rows[0].strip() if rows and rows[0].strip() else None


def _system_mem_gb() -> int:
    """Total system (unified) memory in GB from /proc/meminfo, or 0 on error."""
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // (1024 * 1024)  # kB -> GB
    except (OSError, ValueError, IndexError):
        pass
    return 0


def detect_spark() -> SparkInfo:
    """Probe the host for DGX Spark hardware. Fail-closed on any uncertainty."""
    uname = getattr(os, "uname", None)
    if os.name != "posix" or uname is None or "linux" not in uname().sysname.lower():
        return SparkInfo(is_spark=False)

    gpu_row = _nvidia_query("name")
    if not gpu_row:
        return SparkInfo(is_spark=False)
    gpu_name = gpu_row.split(",")[0].strip()
    if not any(marker in gpu_name.lower() for marker in _SPARK_GPU_MARKERS):
        return SparkInfo(is_spark=False)

    mem_gb = _system_mem_gb()
    if mem_gb < _MIN_UNIFIED_MEM_GB:
        return SparkInfo(is_spark=False)

    return SparkInfo(
        is_spark=True,
        gpu_name=gpu_name,
        unified_mem_gb=mem_gb,
        cuda=_nvidia_query("driver_version"),
    )


_CACHED_SPARK_INFO: SparkInfo | None = None


def get_spark_info() -> SparkInfo:
    """Cached :func:`detect_spark` result for the process lifetime."""
    global _CACHED_SPARK_INFO
    if _CACHED_SPARK_INFO is None:
        _CACHED_SPARK_INFO = detect_spark()
    return _CACHED_SPARK_INFO
