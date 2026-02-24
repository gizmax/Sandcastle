"""Tool Registry - connector system for external service integrations.

Provides tools (Slack, Jira, GitHub, etc.) that workflow steps can use
to interact with external systems. Tools are declared per-step in YAML
and injected into sandboxes at runtime.
"""

from sandcastle.engine.tools.credentials import (
    get_tool_credentials,
    mask_credential,
    validate_tool_credentials,
)
from sandcastle.engine.tools.loader import (
    bundle_tool_files,
    generate_tool_docs,
    generate_tool_schemas,
)
from sandcastle.engine.tools.registry import (
    KNOWN_TOOLS,
    TOOL_REGISTRY,
    ToolDefinition,
    get_tool,
    list_tools,
    validate_tools,
)

__all__ = [
    "KNOWN_TOOLS",
    "TOOL_REGISTRY",
    "ToolDefinition",
    "bundle_tool_files",
    "generate_tool_docs",
    "generate_tool_schemas",
    "get_tool",
    "get_tool_credentials",
    "list_tools",
    "mask_credential",
    "validate_tool_credentials",
    "validate_tools",
]
