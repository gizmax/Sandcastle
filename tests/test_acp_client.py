"""Protocol-layer tests for the ACP client, driven by a fake agent on stdio.

Nothing here needs a real harness, network access or vendor credentials: the
fixture in ``tests/fixtures/fake_acp_agent.py`` is a hand-rolled ACP agent, so
these tests exercise the actual subprocess, the actual framing and the actual
JSON-RPC in both directions.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from sandcastle.engine.acp_client import (
    _BUILTIN_ACP_AGENTS,
    ACP_PROTOCOL_VERSION,
    PERMISSION_KINDS,
    STOP_REASONS,
    AcpError,
    _pick_option,
    build_acp_env,
    resolve_agent_shorthand,
    resolve_permission,
    resolve_workspace_path,
    run_acp_turn,
)
from sandcastle.engine.dag import AcpConfig

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_acp_agent.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(agent_mode: str, **overrides) -> AcpConfig:
    """An AcpConfig pointed at the fake agent in the given behaviour mode.

    The parameter is deliberately not called ``mode``: ``AcpConfig.mode`` is the
    ACP session mode, a different thing entirely.
    """
    defaults = dict(
        command=sys.executable,
        args=[str(FAKE_AGENT), agent_mode],
        timeout=30,
        idle_timeout=0,
        max_output_chars=200000,
    )
    defaults.update(overrides)
    return AcpConfig(**defaults)


async def _turn(cfg: AcpConfig, workspace: Path, message: str = "do the thing", **kwargs):
    return await run_acp_turn(
        cfg,
        message,
        workspace=workspace,
        env=build_acp_env(cfg),
        **kwargs,
    )


def _log_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ===================================================================
# 1. Constants pinned against the spec
# ===================================================================

class TestSpecConstants:
    def test_protocol_version_is_the_integer_one(self):
        """The number on the wire is an integer, not a semver string."""
        assert ACP_PROTOCOL_VERSION == 1
        assert isinstance(ACP_PROTOCOL_VERSION, int)

    def test_stop_reasons_are_the_closed_enum(self):
        assert STOP_REASONS == {
            "end_turn",
            "max_tokens",
            "max_turn_requests",
            "refusal",
            "cancelled",
        }

    def test_permission_kinds_are_the_closed_enum(self):
        assert PERMISSION_KINDS == {
            "allow_once",
            "allow_always",
            "reject_once",
            "reject_always",
        }

    def test_builtin_agents_are_not_registry_downloads(self):
        """The shorthand table resolves to a command, never to a fetch."""
        for name, (command, args) in _BUILTIN_ACP_AGENTS.items():
            assert isinstance(command, str) and command
            assert isinstance(args, list)
            assert "http://" not in " ".join(args)
            assert "https://" not in " ".join(args)


# ===================================================================
# 2. Environment construction (T2: credential exfiltration)
# ===================================================================

class TestBuildAcpEnv:
    def test_parent_credentials_are_not_inherited(self):
        parent = {
            "PATH": "/usr/bin",
            "HOME": "/home/x",
            "ANTHROPIC_API_KEY": "sk-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "DATABASE_URL": "postgres://x",
        }
        env = build_acp_env(AcpConfig(), parent_env=parent)
        assert env["PATH"] == "/usr/bin"
        assert "ANTHROPIC_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "DATABASE_URL" not in env

    def test_env_passthrough_is_an_explicit_allowlist(self):
        parent = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret", "OTHER": "no"}
        env = build_acp_env(
            AcpConfig(env_passthrough=["ANTHROPIC_API_KEY"]), parent_env=parent
        )
        assert env["ANTHROPIC_API_KEY"] == "sk-secret"
        assert "OTHER" not in env

    def test_missing_passthrough_var_is_skipped_not_empty(self):
        env = build_acp_env(
            AcpConfig(env_passthrough=["NOPE"]), parent_env={"PATH": "/usr/bin"}
        )
        assert "NOPE" not in env

    def test_explicit_env_wins(self):
        env = build_acp_env(
            AcpConfig(env={"FOO": "bar"}), parent_env={"PATH": "/usr/bin"}
        )
        assert env["FOO"] == "bar"

    def test_real_parent_env_is_filtered(self, monkeypatch):
        monkeypatch.setenv("SANDCASTLE_ACP_TEST_SECRET", "leaky")
        env = build_acp_env(AcpConfig())
        assert "SANDCASTLE_ACP_TEST_SECRET" not in env
        assert "PATH" in env


# ===================================================================
# 3. Agent shorthand
# ===================================================================

class TestAgentShorthand:
    def test_known_shorthand_expands(self):
        command, args = resolve_agent_shorthand(AcpConfig(agent="claude"))
        assert command == "npx"
        assert any("claude-agent-acp" in a for a in args)

    def test_unknown_shorthand_is_an_error(self):
        with pytest.raises(AcpError) as exc:
            resolve_agent_shorthand(AcpConfig(agent="totally-made-up"))
        assert exc.value.kind == "config"

    def test_command_is_used_verbatim(self):
        command, args = resolve_agent_shorthand(
            AcpConfig(command="/opt/goose", args=["acp", "--verbose"])
        )
        assert command == "/opt/goose"
        assert args == ["acp", "--verbose"]

    def test_neither_is_an_error(self):
        with pytest.raises(AcpError):
            resolve_agent_shorthand(AcpConfig())


# ===================================================================
# 4. Path guards (T3: escape via cwd)
# ===================================================================

class TestWorkspacePaths:
    def test_path_inside_root_is_accepted(self, tmp_path):
        work = tmp_path / "repo"
        work.mkdir()
        assert resolve_workspace_path(str(work), [str(tmp_path)]) == work.resolve()

    def test_root_itself_is_accepted(self, tmp_path):
        assert resolve_workspace_path(str(tmp_path), [str(tmp_path)]) == tmp_path.resolve()

    def test_path_outside_root_is_rejected(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(AcpError) as exc:
            resolve_workspace_path(str(outside), [str(root)])
        assert "outside" in str(exc.value)
        assert exc.value.kind == "config"

    def test_dotdot_is_rejected_before_resolution(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            resolve_workspace_path(f"{tmp_path}/../etc", [str(tmp_path)])
        assert ".." in str(exc.value)

    def test_relative_path_is_rejected(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            resolve_workspace_path("repo", [str(tmp_path)])
        assert "absolute" in str(exc.value)

    def test_symlink_out_of_the_root_is_rejected(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        target = tmp_path / "elsewhere"
        target.mkdir()
        link = root / "escape"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(AcpError):
            resolve_workspace_path(str(link), [str(root)])

    def test_no_configured_roots_disables_the_feature(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            resolve_workspace_path(str(tmp_path), [])
        assert "disabled" in str(exc.value)

    def test_nonexistent_directory_is_rejected(self, tmp_path):
        with pytest.raises(AcpError):
            resolve_workspace_path(str(tmp_path / "nope"), [str(tmp_path)])

    def test_empty_path_is_rejected(self, tmp_path):
        with pytest.raises(AcpError):
            resolve_workspace_path("", [str(tmp_path)])


# ===================================================================
# 5. Permission resolution (T6)
# ===================================================================

class TestResolvePermission:
    def test_default_is_reject(self):
        decision, rule = resolve_permission(AcpConfig(), {"kind": "edit"})
        assert decision == "reject_once"
        assert rule == "default"

    def test_ask_without_a_matching_rule_still_rejects(self):
        """There is no human on this end of an unattended run."""
        decision, rule = resolve_permission(AcpConfig(permission="ask"), {"kind": "edit"})
        assert decision == "reject_once"
        assert "ask" in rule

    def test_rules_are_ordered_first_match_wins(self):
        cfg = AcpConfig(
            permission="reject",
            permission_rules=[
                {"kind": "edit", "decision": "allow_once"},
                {"kind": "edit", "decision": "reject_always"},
            ],
        )
        decision, rule = resolve_permission(cfg, {"kind": "edit"})
        assert decision == "allow_once"
        assert rule == "rule[0]"

    def test_kind_matching(self):
        cfg = AcpConfig(
            permission="allow_once",
            permission_rules=[{"kind": "execute", "decision": "reject_once"}],
        )
        assert resolve_permission(cfg, {"kind": "execute"})[0] == "reject_once"
        assert resolve_permission(cfg, {"kind": "read"})[0] == "allow_once"

    def test_title_matching(self):
        cfg = AcpConfig(
            permission_rules=[
                {"title_matches": "rm -rf", "decision": "reject_always"},
                {"decision": "allow_once"},
            ]
        )
        assert resolve_permission(cfg, {"title": "Run rm -rf /"})[0] == "reject_always"
        assert resolve_permission(cfg, {"title": "Read README"})[0] == "allow_once"

    def test_agent_supplied_option_id_never_influences_the_decision(self):
        """optionId is agent-defined; matching on it works once and breaks next."""
        cfg = AcpConfig(permission_rules=[{"kind": "edit", "decision": "allow_once"}])
        a = resolve_permission(cfg, {"kind": "edit", "optionId": "allow"})
        b = resolve_permission(cfg, {"kind": "edit", "optionId": "definitely-reject"})
        assert a == b == ("allow_once", "rule[0]")

    def test_invalid_rule_decision_is_skipped(self):
        cfg = AcpConfig(
            permission="allow_once",
            permission_rules=[{"kind": "edit", "decision": "maybe"}],
        )
        assert resolve_permission(cfg, {"kind": "edit"}) == ("allow_once", "default")

    def test_pick_option_matches_on_kind_not_id(self):
        options = [
            {"optionId": "xyz-1", "kind": "allow_once"},
            {"optionId": "xyz-2", "kind": "reject_once"},
        ]
        assert _pick_option(options, "reject_once")["optionId"] == "xyz-2"

    def test_pick_option_never_crosses_the_allow_reject_line(self):
        options = [{"optionId": "only", "kind": "allow_once"}]
        assert _pick_option(options, "reject_once") is None

    def test_pick_option_falls_back_within_the_family(self):
        options = [{"optionId": "aa", "kind": "reject_always"}]
        assert _pick_option(options, "reject_once")["optionId"] == "aa"


# ===================================================================
# 6. Happy path against the fake agent
# ===================================================================

@pytest.mark.asyncio
class TestHappyPath:
    async def test_transcript_is_reassembled_from_chunks(self, tmp_path):
        """PromptResponse carries only stopReason - the answer is the stream."""
        result = await _turn(_cfg("echo"), tmp_path)
        assert result.text == "Hello from the fake agent."
        assert result.stop_reason == "end_turn"
        assert result.session_id == "sess_fake_1"
        assert result.protocol_version == 1
        assert result.agent_info == {"name": "fake-acp-agent", "version": "0.0.1"}

    async def test_client_info_and_capabilities_reach_the_agent(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("echo", env={"FAKE_ACP_LOG": str(log)}, filesystem="read")
        await _turn(cfg, tmp_path)
        init = [e for e in _log_events(log) if e["event"] == "initialize"][0]
        assert init["protocolVersion"] == 1
        assert init["clientInfo"]["name"] == "sandcastle"
        assert init["clientCapabilities"]["fs"] == {
            "readTextFile": True,
            "writeTextFile": False,
        }
        assert init["clientCapabilities"]["terminal"] is False

    async def test_cwd_is_sent_to_session_new(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("echo", env={"FAKE_ACP_LOG": str(log)})
        await _turn(cfg, tmp_path)
        new = [e for e in _log_events(log) if e["event"] == "session_new"][0]
        assert new["cwd"] == str(tmp_path)

    async def test_thoughts_are_collected_separately(self, tmp_path):
        result = await _turn(_cfg("thoughts"), tmp_path)
        assert result.text == "answer"
        assert result.thoughts == "thinking hard"

    async def test_tool_calls_and_plan_are_recorded(self, tmp_path):
        result = await _turn(_cfg("tool-calls"), tmp_path)
        assert len(result.tool_calls) == 1
        call = result.tool_calls[0]
        assert call["toolCallId"] == "tc_1"
        assert call["kind"] == "edit"
        # tool_call_update merges onto the same record rather than appending.
        assert call["status"] == "completed"
        assert call["locations"] == [{"path": "/abs/src/foo.py"}]
        assert result.plan == [{"content": "step one"}]

    async def test_modes_are_reported(self, tmp_path):
        result = await _turn(_cfg("echo"), tmp_path)
        assert result.modes == {"current": "code", "available": ["ask", "code"]}

    async def test_set_mode_is_an_opaque_passthrough(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("echo", env={"FAKE_ACP_LOG": str(log)}, mode="architect")
        await _turn(cfg, tmp_path)
        events = [e for e in _log_events(log) if e["event"] == "set_mode"]
        assert events[0]["params"]["modeId"] == "architect"

    async def test_max_output_chars_truncates(self, tmp_path):
        result = await _turn(_cfg("long", max_output_chars=100), tmp_path)
        assert len(result.text) == 100
        assert result.truncated is True

    async def test_env_passthrough_reaches_the_child(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_PASSTHROUGH", "yes")
        monkeypatch.setenv("FAKE_SECRET", "should-not-arrive")
        log = tmp_path / "log.jsonl"
        cfg = _cfg(
            "echo",
            env={"FAKE_ACP_LOG": str(log)},
            env_passthrough=["FAKE_PASSTHROUGH"],
        )
        await _turn(cfg, tmp_path)
        init = [e for e in _log_events(log) if e["event"] == "initialize"][0]
        assert init["env_has_passthrough"] is True
        assert init["env_has_secret"] is False


# ===================================================================
# 7. Version negotiation
# ===================================================================

@pytest.mark.asyncio
class TestVersionNegotiation:
    async def test_mismatch_fails_fast_under_strict_version(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            await _turn(_cfg("version-mismatch"), tmp_path)
        assert exc.value.kind == "version"
        assert "99" in str(exc.value)

    async def test_mismatch_is_tolerated_when_strict_version_is_off(self, tmp_path):
        result = await _turn(_cfg("version-mismatch", strict_version=False), tmp_path)
        assert result.stop_reason == "end_turn"
        assert result.protocol_version == 99


# ===================================================================
# 8. Permissions on the wire
# ===================================================================

@pytest.mark.asyncio
class TestPermissionsOverTheWire:
    async def test_execute_is_rejected_by_default(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("permission", env={"FAKE_ACP_LOG": str(log)})
        result = await _turn(cfg, tmp_path)
        assert result.permissions == [
            {
                "toolCallId": "tc_exec",
                "title": "Run rm -rf /",
                "kind": "execute",
                "decision": "reject_once",
                "optionId": "opt-9z",
                "rule": "default",
            }
        ]
        reply = [e for e in _log_events(log) if e["event"] == "permission_reply"][0]
        assert reply["reply"]["result"]["outcome"] == {
            "outcome": "selected",
            "optionId": "opt-9z",
        }

    async def test_rules_can_allow_edits_while_rejecting_execution(self, tmp_path):
        rules = [
            {"kind": "edit", "decision": "allow_once"},
            {"kind": "execute", "decision": "reject_once"},
        ]
        allowed = await _turn(
            _cfg("permission-edit", permission="ask", permission_rules=rules), tmp_path
        )
        assert allowed.permissions[0]["decision"] == "allow_once"
        assert allowed.permissions[0]["optionId"] == "yy"

        rejected = await _turn(
            _cfg("permission", permission="ask", permission_rules=rules), tmp_path
        )
        assert rejected.permissions[0]["decision"] == "reject_once"


# ===================================================================
# 9. Filesystem and terminal capability gates (T4, T5)
# ===================================================================

@pytest.mark.asyncio
class TestCapabilityGates:
    async def test_fs_read_is_method_not_found_when_filesystem_none(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("fs-probe", env={"FAKE_ACP_LOG": str(log), "FAKE_ACP_FS_TARGET": "/etc/passwd"})
        await _turn(cfg, tmp_path)
        reply = [e for e in _log_events(log) if e["event"] == "fs_read_reply"][0]
        assert reply["reply"]["error"]["code"] == -32601

    async def test_fs_read_outside_the_workspace_is_rejected(self, tmp_path):
        work = tmp_path / "repo"
        work.mkdir()
        log = tmp_path / "log.jsonl"
        cfg = _cfg(
            "fs-probe",
            filesystem="read",
            env={"FAKE_ACP_LOG": str(log), "FAKE_ACP_FS_TARGET": "/etc/passwd"},
        )
        await _turn(cfg, work)
        reply = [e for e in _log_events(log) if e["event"] == "fs_read_reply"][0]
        assert reply["reply"]["error"]["code"] == -32602
        assert "workspace" in reply["reply"]["error"]["message"]

    async def test_fs_read_inside_the_workspace_succeeds(self, tmp_path):
        work = tmp_path / "repo"
        work.mkdir()
        (work / "note.txt").write_text("hello from the workspace")
        log = tmp_path / "log.jsonl"
        cfg = _cfg(
            "fs-probe",
            filesystem="read",
            env={"FAKE_ACP_LOG": str(log), "FAKE_ACP_FS_TARGET": str(work / "note.txt")},
        )
        await _turn(cfg, work)
        reply = [e for e in _log_events(log) if e["event"] == "fs_read_reply"][0]
        assert reply["reply"]["result"]["content"] == "hello from the workspace"

    async def test_fs_write_needs_readwrite_not_read(self, tmp_path):
        work = tmp_path / "repo"
        work.mkdir()
        log = tmp_path / "log.jsonl"
        target = work / "out.txt"
        cfg = _cfg(
            "fs-write",
            filesystem="read",
            env={"FAKE_ACP_LOG": str(log), "FAKE_ACP_FS_TARGET": str(target)},
        )
        await _turn(cfg, work)
        reply = [e for e in _log_events(log) if e["event"] == "fs_write_reply"][0]
        assert reply["reply"]["error"]["code"] == -32601
        assert not target.exists()

    async def test_fs_write_works_under_readwrite(self, tmp_path):
        work = tmp_path / "repo"
        work.mkdir()
        log = tmp_path / "log.jsonl"
        target = work / "out.txt"
        cfg = _cfg(
            "fs-write",
            filesystem="readwrite",
            env={"FAKE_ACP_LOG": str(log), "FAKE_ACP_FS_TARGET": str(target)},
        )
        await _turn(cfg, work)
        assert target.read_text() == "written"

    async def test_terminal_is_always_method_not_found(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("terminal-probe", env={"FAKE_ACP_LOG": str(log)})
        await _turn(cfg, tmp_path)
        reply = [e for e in _log_events(log) if e["event"] == "terminal_reply"][0]
        assert reply["reply"]["error"]["code"] == -32601


# ===================================================================
# 10. Usage and cost
# ===================================================================

@pytest.mark.asyncio
class TestUsage:
    async def test_last_usage_update_wins_and_is_not_summed(self, tmp_path):
        result = await _turn(_cfg("usage"), tmp_path)
        assert result.usage == {
            "used": 53000,
            "size": 200000,
            "cost": {"amount": 0.12, "currency": "USD"},
        }

    async def test_foreign_currency_is_reported_verbatim(self, tmp_path):
        result = await _turn(_cfg("usage-eur"), tmp_path)
        assert result.usage["cost"] == {"amount": 0.99, "currency": "EUR"}


# ===================================================================
# 11. Timeouts, idleness, cancellation, crashes
# ===================================================================

@pytest.fixture
def short_cancel_grace(monkeypatch):
    """Shorten the post-cancel grace period.

    A harness that ignores session/cancel gets _CANCEL_GRACE_SECONDS to answer
    before we stop being polite. That is the right production number and the
    wrong test number - it turns each timeout test into a 12 second wait.
    """
    monkeypatch.setattr("sandcastle.engine.acp_client._CANCEL_GRACE_SECONDS", 1.0)


@pytest.mark.asyncio
class TestFailureModes:
    async def test_hard_timeout_kills_the_agent(self, tmp_path, short_cancel_grace):
        cfg = _cfg("slow", timeout=2, env={"FAKE_ACP_SLEEP": "60"})
        with pytest.raises(AcpError) as exc:
            await _turn(cfg, tmp_path)
        assert exc.value.kind == "timeout"

    async def test_idle_timeout_fires_when_the_stream_goes_quiet(
        self, tmp_path, short_cancel_grace
    ):
        cfg = _cfg("idle", timeout=60, idle_timeout=2, env={"FAKE_ACP_SLEEP": "60"})
        with pytest.raises(AcpError) as exc:
            await _turn(cfg, tmp_path)
        assert exc.value.kind == "idle"

    async def test_cancel_flag_sends_session_cancel_and_answers_pending_permission(
        self, tmp_path
    ):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("cancel", timeout=60, env={"FAKE_ACP_LOG": str(log)})
        flag = {"cancel": False}

        async def cancel_check() -> bool:
            flag["cancel"] = True
            return True

        with pytest.raises(AcpError) as exc:
            await _turn(cfg, tmp_path, cancel_check=cancel_check)
        assert exc.value.kind == "cancelled"

        events = _log_events(log)
        assert any(e["event"] == "session_cancel" for e in events)
        # The spec requires a cancelled turn's pending permission requests to be
        # answered {"outcome": "cancelled"}, not errored and not left hanging.
        reply = [e for e in events if e["event"] == "cancel_permission_reply"]
        assert reply and reply[0]["reply"]["result"]["outcome"] == {"outcome": "cancelled"}

    async def test_crash_surfaces_stderr_not_a_hang(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            await _turn(_cfg("crash"), tmp_path)
        assert exc.value.kind in ("crashed", "protocol")
        # stderr is where a dying harness explains itself; an error without it
        # tells an operator nothing they can act on.
        assert "config missing" in exc.value.stderr_tail

    async def test_dirty_stdout_banner_does_not_break_the_handshake(self, tmp_path):
        """Real CLIs print banners on stdout. Refusing to cope is an outage."""
        result = await _turn(_cfg("dirty-stdout"), tmp_path)
        assert result.text == "Hello from the fake agent."

    async def test_all_junk_stdout_fails_with_a_clear_protocol_error(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            await _turn(_cfg("junk", timeout=20), tmp_path)
        assert exc.value.kind == "protocol"
        assert "non-ACP" in str(exc.value)

    async def test_missing_command_is_a_spawn_error(self, tmp_path):
        cfg = _cfg("echo", command="/nonexistent/acp-agent-binary", args=[])
        with pytest.raises(AcpError) as exc:
            await _turn(cfg, tmp_path)
        assert exc.value.kind == "spawn"

    async def test_missing_session_id_is_a_protocol_error(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            await _turn(_cfg("no-session"), tmp_path)
        assert exc.value.kind == "protocol"
        assert "sessionId" in str(exc.value)

    async def test_unknown_stop_reason_is_a_protocol_error(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            await _turn(_cfg("bad-stop-reason"), tmp_path)
        assert exc.value.kind == "protocol"

    async def test_additional_directories_needs_the_capability(self, tmp_path):
        with pytest.raises(AcpError) as exc:
            await _turn(
                _cfg("echo"), tmp_path, additional_directories=[str(tmp_path)]
            )
        assert exc.value.kind == "capability"
        assert "additionalDirectories" in str(exc.value)

    async def test_additional_directories_pass_when_advertised(self, tmp_path):
        log = tmp_path / "log.jsonl"
        cfg = _cfg("extra-dirs", env={"FAKE_ACP_LOG": str(log)})
        await _turn(cfg, tmp_path, additional_directories=[str(tmp_path)])
        new = [e for e in _log_events(log) if e["event"] == "session_new"][0]
        assert new["additionalDirectories"] == [str(tmp_path)]


@pytest.mark.asyncio
async def test_subprocess_is_reaped_on_timeout(tmp_path, short_cancel_grace):
    """The agent process must be dead, not detached, once the turn gives up."""
    import subprocess

    before = subprocess.run(
        ["pgrep", "-f", "fake_acp_agent.py"], capture_output=True, text=True
    ).stdout.split()
    cfg = _cfg("slow", timeout=2, env={"FAKE_ACP_SLEEP": "120"})
    with pytest.raises(AcpError):
        await _turn(cfg, tmp_path)
    await asyncio.sleep(0.5)
    after = subprocess.run(
        ["pgrep", "-f", "fake_acp_agent.py"], capture_output=True, text=True
    ).stdout.split()
    assert set(after) <= set(before), f"orphaned fake agent: {set(after) - set(before)}"


@pytest.mark.asyncio
async def test_streaming_events_are_emitted_live(tmp_path):
    seen: list[tuple[str, dict]] = []
    await run_acp_turn(
        _cfg("tool-calls"),
        "go",
        workspace=tmp_path,
        env=build_acp_env(_cfg("tool-calls")),
        on_event=lambda kind, payload: seen.append((kind, payload)),
    )
    kinds = [k for k, _ in seen]
    assert "agent_message_chunk" in kinds
    assert "tool_call" in kinds


@pytest.mark.asyncio
async def test_on_event_failure_does_not_break_the_turn(tmp_path):
    def boom(kind, payload):
        raise RuntimeError("subscriber exploded")

    result = await run_acp_turn(
        _cfg("echo"),
        "go",
        workspace=tmp_path,
        env=build_acp_env(_cfg("echo")),
        on_event=boom,
    )
    assert result.stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_outer_cancellation_propagates_and_still_reaps(tmp_path, short_cancel_grace):
    """Cancelling the task must not be laundered into a plain failure.

    The effect ledger reads "cancelled mid-flight" as "we do not know whether
    this landed" and deliberately leaves the claim in_flight. Returning a
    normal exception instead would mark the effect failed, and the next run
    would re-spawn the agent over a possibly half-edited workspace.
    """
    import subprocess

    before = subprocess.run(
        ["pgrep", "-f", "fake_acp_agent.py"], capture_output=True, text=True
    ).stdout.split()

    cfg = _cfg("slow", timeout=60, env={"FAKE_ACP_SLEEP": "120"})
    task = asyncio.ensure_future(_turn(cfg, tmp_path))
    await asyncio.sleep(1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)

    after = subprocess.run(
        ["pgrep", "-f", "fake_acp_agent.py"], capture_output=True, text=True
    ).stdout.split()
    assert set(after) <= set(before), f"orphaned fake agent: {set(after) - set(before)}"
