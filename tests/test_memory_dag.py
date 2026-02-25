"""Tests for memory-related DAG parsing and validation."""

from __future__ import annotations

from sandcastle.engine.dag import (
    MemoryConfig,
    StepMemoryConfig,
    parse_yaml_string,
    validate,
)

# --- Workflow YAML fixtures ---


WORKFLOW_WITH_MEMORY = """
name: standup-summary
description: Daily standup with memory
default_model: sonnet

memory:
  agent: "standup-bot"
  scope: agent
  auto_inject: true
  max_inject: 15

steps:
  - id: gather
    prompt: "Gather standup"
  - id: summarize
    depends_on: [gather]
    memory:
      read: true
      write: true
    prompt: "Summarize with memory context"
"""

WORKFLOW_WITHOUT_MEMORY = """
name: simple-workflow
description: No memory config
default_model: sonnet

steps:
  - id: step1
    prompt: "Do something"
"""

WORKFLOW_MEMORY_BOOL = """
name: bool-memory
description: Step memory as boolean
default_model: sonnet

memory:
  scope: workflow

steps:
  - id: step1
    memory: true
    prompt: "Read and write"
  - id: step2
    memory: false
    prompt: "No memory"
"""

WORKFLOW_GLOBAL_SCOPE = """
name: global-memory
description: Global scope memory
default_model: sonnet

memory:
  scope: global
  max_inject: 5

steps:
  - id: step1
    prompt: "Do something"
"""

WORKFLOW_INVALID_SCOPE = """
name: invalid-scope
description: Bad scope value
default_model: sonnet

memory:
  scope: "invalid_value"

steps:
  - id: step1
    prompt: "Do something"
"""

WORKFLOW_AGENT_SCOPE_NO_NAME = """
name: agent-no-name
description: Agent scope without agent name
default_model: sonnet

memory:
  scope: agent

steps:
  - id: step1
    prompt: "Do something"
"""

WORKFLOW_V2_FULL = """
name: v2-full-config
description: Workflow with all v2 memory fields
default_model: sonnet

memory:
  scope: agent
  agent: research-bot
  auto_inject: true
  max_inject: 25
  max_age_days: 365
  admit_threshold: 0.5
  enrich: true
  conflict_check: true

steps:
  - id: analyze
    memory:
      read: true
      write: true
      admit_threshold: 0.4
    prompt: "Analyze with enriched memory"
"""

WORKFLOW_V2_DEFAULTS = """
name: v2-defaults
description: Workflow where v2 fields use defaults
default_model: sonnet

memory:
  scope: workflow

steps:
  - id: step1
    memory:
      read: true
    prompt: "Simple step"
"""


# --- Parsing tests ---


class TestMemoryParsing:
    def test_workflow_memory_config(self):
        wf = parse_yaml_string(WORKFLOW_WITH_MEMORY)
        assert wf.memory is not None
        assert wf.memory.agent == "standup-bot"
        assert wf.memory.scope == "agent"
        assert wf.memory.auto_inject is True
        assert wf.memory.max_inject == 15

    def test_step_memory_read_write(self):
        wf = parse_yaml_string(WORKFLOW_WITH_MEMORY)
        gather = wf.get_step("gather")
        summarize = wf.get_step("summarize")
        assert gather.memory is None
        assert summarize.memory is not None
        assert summarize.memory.read is True
        assert summarize.memory.write is True

    def test_no_memory_config(self):
        wf = parse_yaml_string(WORKFLOW_WITHOUT_MEMORY)
        assert wf.memory is None
        assert wf.steps[0].memory is None

    def test_step_memory_as_bool_true(self):
        wf = parse_yaml_string(WORKFLOW_MEMORY_BOOL)
        step1 = wf.get_step("step1")
        assert step1.memory is not None
        assert step1.memory.read is True
        assert step1.memory.write is True

    def test_step_memory_as_bool_false(self):
        wf = parse_yaml_string(WORKFLOW_MEMORY_BOOL)
        step2 = wf.get_step("step2")
        assert step2.memory is not None
        assert step2.memory.read is False
        assert step2.memory.write is False

    def test_workflow_scope_default(self):
        wf = parse_yaml_string(WORKFLOW_MEMORY_BOOL)
        assert wf.memory is not None
        assert wf.memory.scope == "workflow"
        assert wf.memory.agent == ""

    def test_global_scope(self):
        wf = parse_yaml_string(WORKFLOW_GLOBAL_SCOPE)
        assert wf.memory is not None
        assert wf.memory.scope == "global"
        assert wf.memory.max_inject == 5

    def test_default_memory_values(self):
        wf = parse_yaml_string(WORKFLOW_GLOBAL_SCOPE)
        assert wf.memory.auto_inject is True  # default
        assert wf.memory.agent == ""  # default


# --- V2 field parsing tests ---


class TestMemoryV2Parsing:
    """Test parsing of new v2 memory fields."""

    def test_v2_full_config_parsed(self):
        wf = parse_yaml_string(WORKFLOW_V2_FULL)
        m = wf.memory
        assert m is not None
        assert m.max_age_days == 365
        assert m.admit_threshold == 0.5
        assert m.enrich is True
        assert m.conflict_check is True

    def test_v2_step_admit_threshold(self):
        wf = parse_yaml_string(WORKFLOW_V2_FULL)
        step = wf.get_step("analyze")
        assert step.memory is not None
        assert step.memory.admit_threshold == 0.4

    def test_v2_defaults_max_age_days(self):
        wf = parse_yaml_string(WORKFLOW_V2_DEFAULTS)
        m = wf.memory
        assert m is not None
        assert m.max_age_days == 0  # default

    def test_v2_defaults_admit_threshold(self):
        wf = parse_yaml_string(WORKFLOW_V2_DEFAULTS)
        m = wf.memory
        assert m.admit_threshold == 0.0  # default

    def test_v2_defaults_enrich(self):
        wf = parse_yaml_string(WORKFLOW_V2_DEFAULTS)
        m = wf.memory
        assert m.enrich is True  # default

    def test_v2_defaults_conflict_check(self):
        wf = parse_yaml_string(WORKFLOW_V2_DEFAULTS)
        m = wf.memory
        assert m.conflict_check is True  # default

    def test_v2_step_admit_threshold_default(self):
        wf = parse_yaml_string(WORKFLOW_V2_DEFAULTS)
        step = wf.get_step("step1")
        assert step.memory is not None
        assert step.memory.admit_threshold == 0.0


# --- Validation tests ---


class TestMemoryValidation:
    def test_valid_workflow_with_memory(self):
        wf = parse_yaml_string(WORKFLOW_WITH_MEMORY)
        errors = validate(wf)
        assert errors == []

    def test_valid_workflow_without_memory(self):
        wf = parse_yaml_string(WORKFLOW_WITHOUT_MEMORY)
        errors = validate(wf)
        assert errors == []

    def test_invalid_scope_value(self):
        wf = parse_yaml_string(WORKFLOW_INVALID_SCOPE)
        errors = validate(wf)
        assert any("Invalid memory scope" in e for e in errors)

    def test_agent_scope_requires_name(self):
        wf = parse_yaml_string(WORKFLOW_AGENT_SCOPE_NO_NAME)
        errors = validate(wf)
        assert any("requires 'agent' name" in e for e in errors)

    def test_global_scope_no_agent_required(self):
        wf = parse_yaml_string(WORKFLOW_GLOBAL_SCOPE)
        errors = validate(wf)
        assert errors == []

    def test_v2_full_config_valid(self):
        wf = parse_yaml_string(WORKFLOW_V2_FULL)
        errors = validate(wf)
        assert errors == []

    def test_v2_defaults_valid(self):
        wf = parse_yaml_string(WORKFLOW_V2_DEFAULTS)
        errors = validate(wf)
        assert errors == []


# --- Dataclass tests ---


class TestMemoryDataclasses:
    def test_step_memory_config_defaults(self):
        cfg = StepMemoryConfig()
        assert cfg.read is True
        assert cfg.write is False

    def test_step_memory_config_admit_threshold_default(self):
        cfg = StepMemoryConfig()
        assert cfg.admit_threshold == 0.0

    def test_memory_config_defaults(self):
        cfg = MemoryConfig()
        assert cfg.agent == ""
        assert cfg.scope == "workflow"
        assert cfg.auto_inject is True
        assert cfg.max_inject == 10

    def test_memory_config_v2_defaults(self):
        cfg = MemoryConfig()
        assert cfg.max_age_days == 0
        assert cfg.admit_threshold == 0.0
        assert cfg.enrich is True
        assert cfg.conflict_check is True

    def test_memory_config_custom_values(self):
        cfg = MemoryConfig(
            scope="agent",
            agent="my-bot",
            max_age_days=180,
            admit_threshold=0.4,
            enrich=False,
            conflict_check=False,
        )
        assert cfg.scope == "agent"
        assert cfg.agent == "my-bot"
        assert cfg.max_age_days == 180
        assert cfg.admit_threshold == 0.4
        assert cfg.enrich is False
        assert cfg.conflict_check is False
