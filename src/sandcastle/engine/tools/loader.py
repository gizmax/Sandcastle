"""Tool loader - bundles connector files and generates documentation/schemas.

Used by the sandbox integration layer to:
1. Upload .mjs connector files into sandboxes
2. Generate tool documentation for Claude runner (text-based tool discovery)
3. Generate OpenAI function_calling schemas for OpenAI-compatible runner
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Directory containing .mjs connector files
_CONNECTORS_DIR = Path(__file__).parent / "connectors"


def bundle_tool_files(tool_names: list[str]) -> dict[str, str]:
    """Read and return connector .mjs files for the given tools.

    Returns a dict of filename -> file_content for uploading into sandboxes.
    Only includes files that exist on disk.
    """
    from sandcastle.engine.tools.registry import get_tool

    files: dict[str, str] = {}
    for name in tool_names:
        try:
            tool = get_tool(name)
        except KeyError:
            logger.warning("Unknown tool '%s', skipping file bundle", name)
            continue

        filepath = _CONNECTORS_DIR / tool.connector_file
        if filepath.exists():
            files[tool.connector_file] = filepath.read_text()
        else:
            logger.warning(
                "Connector file not found: %s (tool: %s)", filepath, name
            )
    return files


def generate_tool_docs(tool_names: list[str]) -> str:
    """Generate human-readable tool documentation for Claude runner.

    The Claude Agent SDK uses Bash tool calls, so tools are documented
    as CLI commands that the agent can execute via Bash.

    Returns a documentation string to append to the step prompt.
    """
    from sandcastle.engine.tools.registry import get_tool

    sections: list[str] = []
    sections.append("# Available Tools")
    sections.append("")
    sections.append("You have access to the following external tools via CLI commands.")
    sections.append("Use the Bash tool to execute them from /home/user/tools/.")
    sections.append("")

    for name in tool_names:
        try:
            tool = get_tool(name)
        except KeyError:
            continue

        sections.append(f"## {tool.name}")
        sections.append(f"{tool.description}")
        sections.append("")

        for func in tool.functions:
            # Build CLI example
            args_parts = []
            for param_name, param_def in func.parameters.get("properties", {}).items():
                if param_name in func.parameters.get("required", []):
                    args_parts.append(f"'<{param_name}>'")
                else:
                    args_parts.append(f"['<{param_name}>']")

            cli_args = " ".join(args_parts)
            sections.append(f"### {func.name}")
            sections.append(f"{func.description}")
            sections.append(f"```bash")
            sections.append(f"node /home/user/tools/{tool.connector_file} {func.name} {cli_args}")
            sections.append(f"```")

            # Parameter details
            props = func.parameters.get("properties", {})
            if props:
                sections.append("Parameters:")
                for pname, pdef in props.items():
                    required = "(required)" if pname in func.parameters.get("required", []) else "(optional)"
                    desc = pdef.get("description", "")
                    sections.append(f"  - {pname} {required}: {desc}")

            sections.append("")

    return "\n".join(sections)


def generate_tool_schemas(tool_names: list[str]) -> list[dict]:
    """Generate OpenAI function_calling tool schemas for the given tools.

    Returns a list of tool definitions in OpenAI function_calling format,
    ready to merge with the runner's built-in tools.
    """
    from sandcastle.engine.tools.registry import get_tool

    schemas: list[dict] = []
    for name in tool_names:
        try:
            tool = get_tool(name)
        except KeyError:
            continue

        for func in tool.functions:
            schemas.append({
                "type": "function",
                "function": {
                    "name": f"{tool.name}_{func.name}",
                    "description": f"[{tool.name}] {func.description}",
                    "parameters": func.parameters,
                },
            })

    return schemas


def generate_tool_schemas_json(tool_names: list[str]) -> str:
    """Generate tool schemas as a JSON string for env var injection."""
    return json.dumps(generate_tool_schemas(tool_names))
