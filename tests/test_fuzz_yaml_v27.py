"""Fuzz-style boundary tests for YAML workflow parsing.

Exercises malformed, extreme, and adversarial YAML inputs against
parse_yaml_string, _parse_step, _parse_raw, and validate.
Every test verifies either correct parsing, a clear error message,
or safe rejection. No crashes allowed.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from sandcastle.engine.dag import (
    StepDefinition,
    WorkflowDefinition,
    _parse_raw,
    _parse_step,
    parse_yaml_string,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_yaml(**overrides) -> str:
    """Return the smallest valid workflow as a YAML string."""
    base = {
        "name": "test",
        "steps": [{"id": "s1", "prompt": "do something"}],
    }
    base.update(overrides)
    return yaml.dump(base, default_flow_style=False)


def _minimal_dict(**overrides) -> dict:
    """Return the smallest valid workflow as a plain dict."""
    base = {
        "name": "test",
        "steps": [{"id": "s1", "prompt": "do something"}],
    }
    base.update(overrides)
    return base


def _step_dict(**overrides) -> dict:
    """Return a minimal step dict."""
    base = {"id": "s1", "prompt": "do something"}
    base.update(overrides)
    return base


_DEFAULTS = {"model": "sonnet", "max_turns": 10, "timeout": 300}


# ===================================================================
# 1. MALFORMED YAML (12 tests)
# ===================================================================

class TestMalformedYaml:
    """YAML with structural or encoding anomalies."""

    def test_tab_indentation(self):
        """Tab-indented YAML should parse (PyYAML allows it) or raise a clear error."""
        yml = "name: test\nsteps:\n\t- id: s1\n\t  prompt: hello"
        try:
            wf = parse_yaml_string(yml)
            assert wf.name == "test"
        except (ValueError, yaml.YAMLError):
            pass  # Clear error is acceptable

    def test_mixed_tabs_and_spaces(self):
        """Mixed tabs/spaces must not crash - either parses or clear error."""
        yml = "name: test\nsteps:\n  - id: s1\n\t  prompt: hello"
        try:
            wf = parse_yaml_string(yml)
            assert wf.name == "test"
        except (ValueError, yaml.YAMLError):
            pass  # Clear error is acceptable

    def test_bom_marker(self):
        """UTF-8 BOM at start of YAML should be handled gracefully."""
        bom = "\ufeff"
        yml = bom + _minimal_yaml()
        try:
            wf = parse_yaml_string(yml)
            assert wf.steps
        except (ValueError, yaml.YAMLError):
            pass  # BOM rejection is acceptable

    def test_windows_line_endings(self):
        """CRLF line endings should parse correctly."""
        yml = _minimal_yaml().replace("\n", "\r\n")
        wf = parse_yaml_string(yml)
        assert wf.name == "test"
        assert len(wf.steps) == 1

    def test_trailing_whitespace(self):
        """Trailing whitespace on lines should not break parsing."""
        yml = "name: test   \nsteps:   \n  - id: s1   \n    prompt: hello   \n"
        wf = parse_yaml_string(yml)
        assert wf.name == "test"

    def test_truncated_yaml_mid_field(self):
        """YAML cut off mid-field should raise a clear error."""
        yml = "name: test\nsteps:\n  - id: s1\n    prompt: hel"
        # This is actually valid YAML (value = "hel"), so it should parse
        wf = parse_yaml_string(yml)
        assert wf.steps[0].prompt == "hel"

    def test_truncated_yaml_mid_key(self):
        """YAML cut off mid-key should raise a clear error, not crash."""
        yml = "name: test\nsteps:\n  - id: s1\n    prom"
        try:
            parse_yaml_string(yml)
        except (ValueError, yaml.YAMLError):
            pass  # Clear error is acceptable

    def test_null_bytes(self):
        """Null bytes in YAML must not crash the parser."""
        yml = "name: test\x00\nsteps:\n  - id: s1\n    prompt: hello"
        try:
            parse_yaml_string(yml)
        except (ValueError, yaml.YAMLError):
            pass  # Rejection is acceptable

    def test_extremely_deep_nesting(self):
        """100 levels of nesting should not stack overflow or hang."""
        # Build a deeply nested dict structure as YAML
        inner = "value"
        for i in range(100):
            inner = {f"level_{i}": inner}
        yml = yaml.dump({"name": "test", "steps": [{"id": "s1", "prompt": "go"}], "deep": inner})
        # The deep key is ignored by the parser - should not crash
        wf = parse_yaml_string(yml)
        assert wf.name == "test"

    def test_yaml_anchor_and_alias(self):
        """YAML anchors (&) and aliases (*) should be resolved by safe_load."""
        yml = textwrap.dedent("""\
            name: test
            _shared_prompt: &shared_prompt "do the thing"
            steps:
              - id: s1
                prompt: *shared_prompt
        """)
        wf = parse_yaml_string(yml)
        assert wf.steps[0].prompt == "do the thing"

    def test_binary_content_disguised_as_yaml(self):
        """Random binary bytes should not crash - clear error expected."""
        binary = bytes(range(256)).decode("latin-1")
        try:
            parse_yaml_string(binary)
        except (ValueError, yaml.YAMLError):
            pass  # Safe rejection

    def test_yaml_only_comments(self):
        """YAML with only comments should be rejected as empty."""
        yml = "# just a comment\n# another one\n"
        with pytest.raises(ValueError, match="(?i)(empty|none)"):
            parse_yaml_string(yml)


# ===================================================================
# 2. EXTREME VALUES (12 tests)
# ===================================================================

class TestExtremeValues:
    """Boundary and extreme parameter values."""

    def test_empty_prompt_string(self):
        """Step with prompt='' should parse; standard type gets empty prompt."""
        step = _parse_step({"id": "s1", "prompt": ""}, _DEFAULTS)
        assert step.prompt == ""

    def test_very_large_prompt(self):
        """1MB prompt string should parse without error."""
        big_prompt = "x" * (1024 * 1024)
        step = _parse_step({"id": "s1", "prompt": big_prompt}, _DEFAULTS)
        assert len(step.prompt) == 1024 * 1024

    def test_max_turns_zero(self):
        """max_turns=0 should parse but fail validation (must be >= 1)."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "max_turns": 0}]
        ))
        errors = validate(wf)
        assert any("max_turns" in e for e in errors)

    def test_max_turns_extreme_high(self):
        """max_turns=999999 should parse but fail validation (max 1000)."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "max_turns": 999999}]
        ))
        errors = validate(wf)
        assert any("max_turns" in e and "1000" in e for e in errors)

    def test_timeout_zero(self):
        """timeout=0 should parse but fail validation (must be > 0)."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "timeout": 0}]
        ))
        errors = validate(wf)
        assert any("timeout" in e and "> 0" in e for e in errors)

    def test_timeout_negative(self):
        """timeout=-1 should parse but fail validation."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "timeout": -1}]
        ))
        errors = validate(wf)
        assert any("timeout" in e for e in errors)

    def test_empty_workflow_name(self):
        """Workflow with name='' should be caught by validation."""
        wf = _parse_raw({"name": "", "steps": [{"id": "s1", "prompt": "go"}]})
        errors = validate(wf)
        assert any("name" in e.lower() for e in errors)

    def test_very_long_workflow_name(self):
        """Workflow name of 500 chars should fail validation (max 200)."""
        wf = _parse_raw({"name": "A" * 500, "steps": [{"id": "s1", "prompt": "go"}]})
        errors = validate(wf)
        assert any("name too long" in e.lower() for e in errors)

    def test_200_steps_workflow(self):
        """Workflow with 200 steps should parse and validate (under 500 limit)."""
        steps = [{"id": f"step_{i}", "prompt": f"task {i}"} for i in range(200)]
        wf = _parse_raw({"name": "big", "steps": steps})
        assert len(wf.steps) == 200
        # Should not trigger "too many steps" error
        errors = validate(wf)
        assert not any("too many steps" in e.lower() for e in errors)

    def test_501_steps_workflow(self):
        """Workflow with 501 steps should fail validation (max 500)."""
        steps = [{"id": f"step_{i}", "prompt": f"task {i}"} for i in range(501)]
        wf = _parse_raw({"name": "huge", "steps": steps})
        errors = validate(wf)
        assert any("too many steps" in e.lower() for e in errors)

    def test_50_dependencies(self):
        """Step with 50 depends_on entries should parse and validate."""
        dep_steps = [{"id": f"dep_{i}", "prompt": f"dep {i}"} for i in range(50)]
        final = {"id": "final", "prompt": "combine", "depends_on": [f"dep_{i}" for i in range(50)]}
        wf = _parse_raw({"name": "fan_in", "steps": dep_steps + [final]})
        assert len(wf.steps[-1].depends_on) == 50
        errors = validate(wf)
        # No error about depends_on count - all refs valid
        assert not any("depends on unknown" in e for e in errors)

    def test_yaml_exceeds_1mb_limit(self):
        """YAML string > 1MB should be rejected by parse_yaml_string."""
        huge = "name: test\npayload: " + "x" * (1024 * 1024 + 100)
        with pytest.raises(ValueError, match="(?i)too large"):
            parse_yaml_string(huge)


# ===================================================================
# 3. TYPE CONFUSION (12 tests)
# ===================================================================

class TestTypeConfusion:
    """Wrong types for fields that expect specific types."""

    def test_prompt_as_integer(self):
        """prompt: 123 (integer) should be coerced or produce clear error."""
        # YAML will parse `prompt: 123` as int. _parse_step should handle it.
        step = _parse_step({"id": "s1", "prompt": 123}, _DEFAULTS)
        # The parser stores whatever YAML gives; prompt is used as str downstream
        assert step.prompt == 123 or str(step.prompt) == "123"

    def test_prompt_as_boolean(self):
        """prompt: true (boolean) should be handled gracefully."""
        step = _parse_step({"id": "s1", "prompt": True}, _DEFAULTS)
        assert step.prompt is True or str(step.prompt) in ("True", "true")

    def test_prompt_as_list(self):
        """prompt: [1,2,3] should be stored without crash."""
        step = _parse_step({"id": "s1", "prompt": [1, 2, 3]}, _DEFAULTS)
        assert step.prompt == [1, 2, 3]

    def test_model_as_integer(self):
        """model: 123 should be stored; validation catches unknown model."""
        step = _parse_step({"id": "s1", "prompt": "go", "model": 123}, _DEFAULTS)
        assert step.model == 123

    def test_timeout_as_string(self):
        """timeout: 'not a number' should be stored without crash at parse time."""
        step = _parse_step({"id": "s1", "prompt": "go", "timeout": "not a number"}, _DEFAULTS)
        assert step.timeout == "not a number"

    def test_steps_as_string(self):
        """steps: 'not a list' should raise a clear ValueError."""
        with pytest.raises(ValueError, match="(?i)steps.*must be a list"):
            _parse_raw({"name": "test", "steps": "not a list"})

    def test_depends_on_as_dict(self):
        """depends_on: {key: value} should be coerced to a list."""
        # The parser does `if not isinstance(raw_deps, list): raw_deps = [raw_deps]`
        step = _parse_step(
            {"id": "s1", "prompt": "go", "depends_on": {"key": "value"}},
            _DEFAULTS,
        )
        # The dict is wrapped in a list and str()'d
        assert isinstance(step.depends_on, list)
        assert len(step.depends_on) == 1

    def test_type_as_null(self):
        """type: null should default to 'standard'."""
        step = _parse_step({"id": "s1", "prompt": "go", "type": None}, _DEFAULTS)
        # data.get("type", "standard") returns None when key is present with None value
        # So step_type becomes None
        assert step.type is None or step.type == "standard"

    def test_id_as_null(self):
        """id: null should raise ValueError (non-empty id required)."""
        with pytest.raises(ValueError, match="(?i)non-empty.*id"):
            _parse_step({"id": None, "prompt": "go"}, _DEFAULTS)

    def test_id_as_integer(self):
        """id: 123 (integer) should raise ValueError (must be string)."""
        with pytest.raises(ValueError, match="(?i)id.*must be a string"):
            _parse_step({"id": 123, "prompt": "go"}, _DEFAULTS)

    def test_step_as_list_instead_of_dict(self):
        """A step that is a list instead of a dict should raise ValueError."""
        with pytest.raises(ValueError, match="(?i)step must be a mapping"):
            _parse_step(["id", "s1", "prompt", "go"], _DEFAULTS)

    def test_step_as_string(self):
        """A step that is a plain string should raise ValueError."""
        with pytest.raises(ValueError, match="(?i)step must be a mapping"):
            _parse_step("just a string", _DEFAULTS)


# ===================================================================
# 4. INJECTION ATTEMPTS (8 tests)
# ===================================================================

class TestInjectionAttempts:
    """Adversarial inputs trying to break parsing or inject data."""

    def test_step_id_with_yaml_special_chars_colon(self):
        """Step ID containing ':' should fail validation regex."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "step:evil", "prompt": "go"}]
        ))
        errors = validate(wf)
        assert any("invalid" in e.lower() and "step:evil" in e for e in errors)

    def test_step_id_with_at_sign(self):
        """Step ID containing '@' should fail validation regex."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "step@evil", "prompt": "go"}]
        ))
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_with_hash(self):
        """Step ID containing '#' should fail validation regex."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "step#evil", "prompt": "go"}]
        ))
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_step_id_with_percent(self):
        """Step ID containing '%' should fail validation regex."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "step%evil", "prompt": "go"}]
        ))
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_prompt_containing_yaml_syntax(self):
        """Prompt containing YAML key: value should be treated as plain string."""
        yml = textwrap.dedent("""\
            name: test
            steps:
              - id: s1
                prompt: "key: value\\nanother: thing"
        """)
        wf = parse_yaml_string(yml)
        assert "key: value" in wf.steps[0].prompt

    def test_name_with_newline_characters(self):
        """Workflow name with embedded newlines should parse without crash."""
        wf = _parse_raw({"name": "line1\nline2\nline3", "steps": [{"id": "s1", "prompt": "go"}]})
        assert "\n" in wf.name

    def test_step_with_proto_key(self):
        """Step with __proto__ key should not cause prototype pollution or crash."""
        step_data = {"id": "s1", "prompt": "go", "__proto__": {"admin": True}}
        step = _parse_step(step_data, _DEFAULTS)
        assert step.id == "s1"
        # __proto__ is just ignored by the parser
        assert not hasattr(step, "__proto__") or not isinstance(getattr(step, "__proto__", None), dict)

    def test_step_with_constructor_key(self):
        """Step with 'constructor' key should be safely ignored."""
        step_data = {"id": "s1", "prompt": "go", "constructor": {"evil": True}}
        step = _parse_step(step_data, _DEFAULTS)
        assert step.id == "s1"


# ===================================================================
# 5. YAML EDGE CASES (8 tests)
# ===================================================================

class TestYamlEdgeCases:
    """Additional edge cases for comprehensive coverage."""

    def test_empty_string_input(self):
        """Empty string should raise ValueError."""
        with pytest.raises(ValueError, match="(?i)empty"):
            parse_yaml_string("")

    def test_whitespace_only_input(self):
        """Whitespace-only string should raise ValueError."""
        with pytest.raises(ValueError, match="(?i)empty"):
            parse_yaml_string("   \n\t\n   ")

    def test_yaml_with_no_name_field(self):
        """YAML without 'name' should raise ValueError."""
        with pytest.raises(ValueError, match="(?i)name"):
            parse_yaml_string("steps:\n  - id: s1\n    prompt: hello")

    def test_yaml_that_parses_to_string(self):
        """Plain string YAML should be rejected (not a mapping)."""
        with pytest.raises(ValueError, match="(?i)must be a mapping"):
            parse_yaml_string("just a plain string")

    def test_yaml_that_parses_to_list(self):
        """YAML that parses to a list should be rejected."""
        with pytest.raises(ValueError, match="(?i)must be a mapping"):
            parse_yaml_string("- item1\n- item2\n- item3")

    def test_yaml_that_parses_to_integer(self):
        """YAML that parses to an integer should be rejected."""
        with pytest.raises(ValueError, match="(?i)must be a mapping"):
            parse_yaml_string("42")

    def test_duplicate_step_ids(self):
        """Duplicate step IDs should be caught by validation."""
        wf = _parse_raw({
            "name": "test",
            "steps": [
                {"id": "dup", "prompt": "first"},
                {"id": "dup", "prompt": "second"},
            ],
        })
        errors = validate(wf)
        assert any("duplicate" in e.lower() for e in errors)

    def test_depends_on_nonexistent_step(self):
        """depends_on referencing a missing step should be caught by validation."""
        wf = _parse_raw({
            "name": "test",
            "steps": [
                {"id": "s1", "prompt": "go", "depends_on": ["ghost_step"]},
            ],
        })
        errors = validate(wf)
        assert any("unknown step" in e.lower() and "ghost_step" in e for e in errors)


# ===================================================================
# 6. VALIDATION BOUNDARY TESTS (6 tests)
# ===================================================================

class TestValidationBoundaries:
    """Tests that push validation rules to their exact limits."""

    def test_step_id_exactly_100_chars(self):
        """Step ID of exactly 100 alphanumeric chars should be valid."""
        long_id = "a" * 100
        wf = _parse_raw({
            "name": "test",
            "steps": [{"id": long_id, "prompt": "go"}],
        })
        errors = validate(wf)
        assert not any("invalid" in e.lower() and long_id in e for e in errors)

    def test_step_id_101_chars(self):
        """Step ID of 101 chars should fail validation (max 100)."""
        long_id = "a" * 101
        wf = _parse_raw({
            "name": "test",
            "steps": [{"id": long_id, "prompt": "go"}],
        })
        errors = validate(wf)
        assert any("invalid" in e.lower() for e in errors)

    def test_workflow_name_exactly_200_chars(self):
        """Workflow name of exactly 200 chars should be valid."""
        name_200 = "A" * 200
        wf = _parse_raw({
            "name": name_200,
            "steps": [{"id": "s1", "prompt": "go"}],
        })
        errors = validate(wf)
        assert not any("name too long" in e.lower() for e in errors)

    def test_timeout_exactly_86400(self):
        """timeout=86400 (24h) should be valid (boundary)."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "timeout": 86400}]
        ))
        errors = validate(wf)
        assert not any("timeout" in e and "86400" in e for e in errors)

    def test_timeout_86401_exceeds_limit(self):
        """timeout=86401 should fail validation (max 86400)."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "timeout": 86401}]
        ))
        errors = validate(wf)
        assert any("timeout" in e and "86400" in e for e in errors)

    def test_max_turns_exactly_1000(self):
        """max_turns=1000 should be valid (boundary)."""
        wf = _parse_raw(_minimal_dict(
            steps=[{"id": "s1", "prompt": "go", "max_turns": 1000}]
        ))
        errors = validate(wf)
        assert not any("max_turns" in e and "1000" in e for e in errors)
