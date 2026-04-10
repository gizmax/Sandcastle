"""Agent execution runtime abstraction.

Supports multiple backends: Anthropic Managed Agents, OpenAI (future),
local execution via oMLX/Ollama. AutoRuntime selects the best available.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class AgentRuntime(ABC):
    """Abstract base for agent execution runtimes."""

    name: str = ""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check whether this runtime can be used in the current environment."""
        ...

    @abstractmethod
    async def execute(
        self,
        system_prompt: str,
        tools: list[str],
        packages: list[str],
        message: str,
        model: str,
        timeout: int,
        network: str,
    ) -> dict:
        """Execute agent and return result dict.

        Returns:
            dict with keys: output (str), tokens_in (int),
            tokens_out (int), runtime (str).
        """
        ...


class AnthropicRuntime(AgentRuntime):
    """Anthropic Managed Agents - cloud containers with bash, web, files."""

    name = "anthropic"

    async def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    async def execute(
        self,
        system_prompt: str,
        tools: list[str],
        packages: list[str],
        message: str,
        model: str,
        timeout: int,
        network: str,
    ) -> dict:
        import httpx

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for AnthropicRuntime")

        base_url = "https://api.anthropic.com/v1"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "managed-agents-2026-04-01",
            "content-type": "application/json",
        }

        session_id: str | None = None

        async with httpx.AsyncClient(timeout=timeout + 60) as client:
            # 1. Create environment
            env_body: dict[str, Any] = {
                "name": "sandcastle-agent-env",
                "network_access": network == "unrestricted",
            }
            if packages:
                env_body["packages"] = packages

            env_resp = await client.post(
                f"{base_url}/environments", headers=headers, json=env_body,
            )
            if env_resp.status_code != 200:
                raise RuntimeError(
                    f"Environment creation failed: {env_resp.status_code} {env_resp.text[:200]}"
                )
            env_id = env_resp.json()["id"]

            # 2. Create agent
            agent_payload: dict[str, Any] = {
                "name": "sandcastle-agent",
                "model": model,
                "tools": [{"type": "agent_toolset_20260401"}],
            }
            if system_prompt:
                agent_payload["system"] = system_prompt

            agent_resp = await client.post(
                f"{base_url}/agents", headers=headers, json=agent_payload,
            )
            if agent_resp.status_code != 200:
                raise RuntimeError(
                    f"Agent creation failed: {agent_resp.status_code} {agent_resp.text[:200]}"
                )
            agent_id = agent_resp.json()["id"]

            # 3. Create session
            session_resp = await client.post(
                f"{base_url}/sessions",
                headers=headers,
                json={"agent": agent_id, "environment_id": env_id},
            )
            if session_resp.status_code != 200:
                raise RuntimeError(
                    f"Session creation failed: {session_resp.status_code} {session_resp.text[:200]}"
                )
            session_id = session_resp.json()["id"]

            try:
                # 4. Send message
                await client.post(
                    f"{base_url}/sessions/{session_id}/events",
                    headers=headers,
                    json={
                        "events": [{
                            "type": "user.message",
                            "content": [{"type": "text", "text": message}],
                        }],
                    },
                )

                # 5. Stream response
                result_text = ""
                tokens_in = 0
                tokens_out = 0

                async with client.stream(
                    "GET",
                    f"{base_url}/sessions/{session_id}/stream",
                    headers=headers,
                ) as stream:
                    async for line in stream.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            event = json.loads(line[6:])
                        except (json.JSONDecodeError, ValueError):
                            continue

                        etype = event.get("type", "")
                        if etype == "agent.message":
                            for block in event.get("content", []):
                                if block.get("type") == "text":
                                    result_text += block.get("text", "")

                        usage = event.get("usage")
                        if usage:
                            tokens_in += usage.get("input_tokens", 0)
                            tokens_out += usage.get("output_tokens", 0)

                        if etype in ("session.status_idle", "session.terminated"):
                            break

                return {
                    "output": result_text,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "runtime": "anthropic",
                }
            finally:
                if session_id:
                    try:
                        await client.delete(
                            f"{base_url}/sessions/{session_id}",
                            headers=headers,
                        )
                    except Exception:
                        logger.debug("Failed to clean up session %s", session_id)


class LocalRuntime(AgentRuntime):
    """Local agent execution via Ollama - no cloud, no cost."""

    name = "local"

    async def is_available(self) -> bool:
        try:
            import httpx
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    async def execute(
        self,
        system_prompt: str,
        tools: list[str],
        packages: list[str],
        message: str,
        model: str,
        timeout: int,
        network: str,
    ) -> dict:
        import httpx

        ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        ollama_model = os.environ.get("OLLAMA_MODEL", "llama3.2")

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})

        async with httpx.AsyncClient(timeout=timeout + 30) as client:
            resp = await client.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": messages,
                    "stream": False,
                },
            )

            if resp.status_code != 200:
                raise RuntimeError(f"Ollama call failed: {resp.status_code} {resp.text[:200]}")

            data = resp.json()
            return {
                "output": data.get("message", {}).get("content", ""),
                "tokens_in": data.get("prompt_eval_count", 0),
                "tokens_out": data.get("eval_count", 0),
                "runtime": "local",
            }


class AutoRuntime(AgentRuntime):
    """Automatically selects the best available runtime.

    Prefers Anthropic (cloud containers with tools) over local (Ollama).
    """

    name = "auto"

    _runtimes: list[AgentRuntime] = [AnthropicRuntime(), LocalRuntime()]

    async def is_available(self) -> bool:
        for rt in self._runtimes:
            if await rt.is_available():
                return True
        return False

    async def execute(
        self,
        system_prompt: str = "",
        tools: list[str] | None = None,
        packages: list[str] | None = None,
        message: str = "",
        model: str = "claude-sonnet-4-6",
        timeout: int = 600,
        network: str = "unrestricted",
    ) -> dict:
        for rt in self._runtimes:
            if await rt.is_available():
                logger.info("AutoRuntime selected: %s", rt.name)
                return await rt.execute(
                    system_prompt=system_prompt,
                    tools=tools or [],
                    packages=packages or [],
                    message=message,
                    model=model,
                    timeout=timeout,
                    network=network,
                )
        raise RuntimeError(
            "No agent runtime available. Set ANTHROPIC_API_KEY or start Ollama."
        )


# Registry of all available runtimes
RUNTIMES: dict[str, AgentRuntime] = {
    "auto": AutoRuntime(),
    "anthropic": AnthropicRuntime(),
    "local": LocalRuntime(),
}


def get_runtime(name: str) -> AgentRuntime:
    """Get a runtime by name.

    Args:
        name: Runtime identifier - "auto", "anthropic", or "local".

    Returns:
        AgentRuntime instance.

    Raises:
        ValueError: If the runtime name is not recognized.
    """
    rt = RUNTIMES.get(name)
    if not rt:
        raise ValueError(
            f"Unknown runtime '{name}'. Available: {', '.join(sorted(RUNTIMES))}"
        )
    return rt
