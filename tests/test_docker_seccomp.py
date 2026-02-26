"""Tests for Docker seccomp profiles and container hardening."""

from __future__ import annotations

import json
from pathlib import Path

from sandcastle.engine.backends import DockerBackend, create_backend

# Path to the bundled seccomp profile
_ENGINE_DIR = Path(__file__).parent.parent / "src" / "sandcastle" / "engine"
SECCOMP_PATH = _ENGINE_DIR / "seccomp-default.json"


class TestSeccompProfile:
    """The bundled seccomp profile should be valid and block dangerous syscalls."""

    def test_file_exists(self):
        assert SECCOMP_PATH.exists(), f"seccomp-default.json not found at {SECCOMP_PATH}"

    def test_valid_json(self):
        data = json.loads(SECCOMP_PATH.read_text())
        assert "defaultAction" in data
        assert "syscalls" in data

    def test_default_action_is_allow(self):
        data = json.loads(SECCOMP_PATH.read_text())
        assert data["defaultAction"] == "SCMP_ACT_ALLOW"

    def test_blocks_ptrace(self):
        data = json.loads(SECCOMP_PATH.read_text())
        blocked = set()
        for rule in data["syscalls"]:
            if rule["action"] == "SCMP_ACT_ERRNO":
                blocked.update(rule["names"])
        assert "ptrace" in blocked

    def test_blocks_mount(self):
        data = json.loads(SECCOMP_PATH.read_text())
        blocked = set()
        for rule in data["syscalls"]:
            if rule["action"] == "SCMP_ACT_ERRNO":
                blocked.update(rule["names"])
        assert "mount" in blocked

    def test_blocks_kexec(self):
        data = json.loads(SECCOMP_PATH.read_text())
        blocked = set()
        for rule in data["syscalls"]:
            if rule["action"] == "SCMP_ACT_ERRNO":
                blocked.update(rule["names"])
        assert "kexec_load" in blocked

    def test_blocks_keyctl(self):
        data = json.loads(SECCOMP_PATH.read_text())
        blocked = set()
        for rule in data["syscalls"]:
            if rule["action"] == "SCMP_ACT_ERRNO":
                blocked.update(rule["names"])
        assert "keyctl" in blocked


class TestDockerBackendHardening:
    """DockerBackend should include security hardening fields."""

    def test_default_hardening_params(self):
        backend = DockerBackend()
        assert backend._pids_limit == 100
        assert backend._cpu_period == 100_000
        assert backend._cpu_quota == 50_000
        assert backend._seccomp_profile == ""

    def test_custom_hardening_params(self):
        backend = DockerBackend(
            seccomp_profile="/custom/profile.json",
            pids_limit=200,
            cpu_period=50_000,
            cpu_quota=25_000,
        )
        assert backend._seccomp_profile == "/custom/profile.json"
        assert backend._pids_limit == 200
        assert backend._cpu_period == 50_000
        assert backend._cpu_quota == 25_000

    def test_factory_passes_hardening_params(self):
        backend = create_backend(
            "docker",
            docker_seccomp_profile="/test/profile.json",
            docker_pids_limit=50,
            docker_cpu_period=200_000,
            docker_cpu_quota=100_000,
        )
        assert isinstance(backend, DockerBackend)
        assert backend._seccomp_profile == "/test/profile.json"
        assert backend._pids_limit == 50
        assert backend._cpu_period == 200_000
        assert backend._cpu_quota == 100_000


class TestDockerConfig:
    """Config settings for Docker hardening."""

    def test_config_defaults(self):
        from sandcastle.config import Settings

        s = Settings(
            _env_file=None,
            anthropic_api_key="test",
            e2b_api_key="test",
        )
        assert s.docker_seccomp_profile == ""
        assert s.docker_pids_limit == 100
        assert s.docker_cpu_period == 100_000
        assert s.docker_cpu_quota == 50_000
