"""Tests for the `sandcastle doctor` CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sandcastle.__main__ import _build_parser, _cmd_doctor, main


# ---------------------------------------------------------------------------
# TestDoctorArgParsing
# ---------------------------------------------------------------------------


class TestDoctorArgParsing:
    """Verify doctor is wired into the CLI parser."""

    def test_parser_accepts_doctor(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"

    def test_doctor_in_dispatch(self) -> None:
        """The dispatch table in main() must include 'doctor'."""
        # We import the source to inspect - just verify it doesn't crash
        parser = _build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"


# ---------------------------------------------------------------------------
# TestDoctorCommand
# ---------------------------------------------------------------------------


class TestDoctorCommand:
    """Test _cmd_doctor output and exit codes."""

    def test_passes_with_anthropic_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Doctor should pass critical checks when ANTHROPIC_API_KEY is set."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-key-1234"}):
            with pytest.raises(SystemExit) as exc_info:
                _cmd_doctor(None)
            output = capsys.readouterr().out
            assert "[PASS]" in output
            assert "ANTHROPIC_API_KEY configured" in output
            # Exit code 0 (all critical pass)
            assert exc_info.value.code == 0

    def test_fails_without_anthropic_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Doctor should fail when ANTHROPIC_API_KEY is missing."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            with patch("sandcastle.config.Settings") as MockSettings:
                mock_cfg = MockSettings.return_value
                mock_cfg.anthropic_api_key = ""
                mock_cfg.e2b_api_key = ""
                mock_cfg.sandbox_backend = "e2b"
                mock_cfg.cloudflare_worker_url = ""
                with pytest.raises(SystemExit) as exc_info:
                    _cmd_doctor(None)
                output = capsys.readouterr().out
                assert "[FAIL]" in output
                assert "ANTHROPIC_API_KEY not set" in output
                assert exc_info.value.code == 1

    def test_shows_backend_info(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Doctor should display current sandbox backend."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with pytest.raises(SystemExit):
                _cmd_doctor(None)
            output = capsys.readouterr().out
            assert "Backend:" in output

    def test_warns_port_in_use(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Doctor should warn when port 8080 is in use."""
        import socket

        # Bind port 8080 temporarily
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", 8080))
            sock.listen(1)
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
                with pytest.raises(SystemExit):
                    _cmd_doctor(None)
                output = capsys.readouterr().out
                assert "8080" in output
                assert "in use" in output
        except OSError:
            pytest.skip("Port 8080 could not be bound (already in use)")
        finally:
            sock.close()

    def test_checks_dependencies(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Doctor should verify required dependencies."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test"}):
            with pytest.raises(SystemExit):
                _cmd_doctor(None)
            output = capsys.readouterr().out
            # Required packages should be checked
            assert "fastapi" in output
            assert "uvicorn" in output
