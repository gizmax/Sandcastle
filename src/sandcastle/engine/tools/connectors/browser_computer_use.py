"""Computer Use automation loop using Anthropic's computer_use beta.

Drives a browser via screenshot-action cycles: take a screenshot, send it to
Claude with the computer_use tool, execute the returned actions, and repeat
until the model signals completion or the iteration limit is reached.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

# Viewport dimensions must match the browser page size
DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 720


async def computer_use_loop(
    task_description: str,
    start_url: str,
    screenshot_fn: Callable[[], Awaitable[str]],
    action_fn: Callable[..., Awaitable[dict]],
    api_key: str,
    max_iterations: int = 30,
    model: str = "claude-sonnet-4-20250514",
) -> dict[str, Any]:
    """Run a computer use automation loop.

    Args:
        task_description: Natural language description of the task to complete.
        start_url: The URL that has already been opened in the browser.
        screenshot_fn: Async callable that returns a base64-encoded PNG string.
        action_fn: Async callable(action_type, **kwargs) that executes a
            browser action and returns a result dict.
        api_key: Anthropic API key.
        max_iterations: Maximum number of screenshot-action cycles.
        model: Claude model to use for reasoning.

    Returns:
        dict with keys: status ("completed" | "timeout"), message, iterations.
    """
    messages: list[dict[str, Any]] = []

    system_prompt = (
        "You are a browser automation agent. Complete the task by clicking, "
        "typing, scrolling, and navigating. When the task is complete, respond "
        "with a text block saying TASK_COMPLETE followed by a summary of what "
        "was accomplished."
    )

    tools = [
        {
            "type": "computer_20241022",
            "name": "computer",
            "display_width_px": DISPLAY_WIDTH,
            "display_height_px": DISPLAY_HEIGHT,
        }
    ]

    for iteration in range(max_iterations):
        # 1. Take a screenshot of the current page state
        screenshot_b64 = await screenshot_fn()

        # 2. Build the user message with the screenshot
        if not messages:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Task: {task_description}\n\n"
                            f"I've opened {start_url}. Please complete the "
                            "task by interacting with the page."
                        ),
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                ],
            })
        else:
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Here's the current state of the page:",
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": screenshot_b64,
                        },
                    },
                ],
            })

        # 3. Call Claude with the computer_use tool
        logger.debug(
            "Computer use iteration %d/%d - sending screenshot",
            iteration + 1,
            max_iterations,
        )
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "computer-use-2024-10-22",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "tools": tools,
                    "messages": messages,
                },
            )
            resp.raise_for_status()

        result = resp.json()
        assistant_content = result["content"]
        messages.append({"role": "assistant", "content": assistant_content})

        # 4. Process the response blocks
        for block in assistant_content:
            # Check for task completion signal
            if block["type"] == "text" and "TASK_COMPLETE" in block.get("text", ""):
                logger.info(
                    "Computer use completed in %d iterations", iteration + 1
                )
                return {
                    "status": "completed",
                    "message": block["text"],
                    "iterations": iteration + 1,
                }

            # Execute tool use actions
            if block["type"] == "tool_use" and block["name"] == "computer":
                action_input = block["input"]
                action_type = action_input.get("action", "")

                try:
                    await _dispatch_action(action_fn, action_type, action_input)
                    tool_result_content = "Action executed."
                except Exception as exc:
                    logger.warning(
                        "Action %s failed: %s", action_type, exc
                    )
                    tool_result_content = f"Action failed: {exc}"

                # Report tool result back to Claude
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": block["id"],
                            "content": tool_result_content,
                        }
                    ],
                })

    logger.warning("Computer use reached max iterations (%d)", max_iterations)
    return {
        "status": "timeout",
        "message": f"Reached max iterations ({max_iterations})",
        "iterations": max_iterations,
    }


async def _dispatch_action(
    action_fn: Callable[..., Awaitable[dict]],
    action_type: str,
    action_input: dict[str, Any],
) -> dict[str, Any]:
    """Map Anthropic computer_use actions to browser action_fn calls.

    The Anthropic computer_use tool returns actions like:
        {"action": "mouse_move", "coordinate": [x, y]}
        {"action": "left_click"}
        {"action": "type", "text": "hello"}
        {"action": "key", "text": "Enter"}
        {"action": "screenshot"}
    """
    coord = action_input.get("coordinate")

    if action_type in ("mouse_move", "left_click", "right_click", "middle_click",
                        "double_click"):
        x = coord[0] if coord else None
        y = coord[1] if coord else None

        if action_type == "mouse_move":
            return await action_fn("mouse_move", x=x, y=y)
        elif action_type in ("left_click", "right_click", "middle_click",
                             "double_click"):
            # Move to coordinates first, then click
            if x is not None and y is not None:
                return await action_fn("mouse_click", x=x, y=y)
            return await action_fn("mouse_click", x=0, y=0)

    elif action_type == "type":
        text = action_input.get("text", "")
        return await action_fn("type_text_raw", text=text)

    elif action_type == "key":
        key = action_input.get("text", "")
        return await action_fn("key_press", text=key)

    elif action_type == "screenshot":
        return await action_fn("screenshot_raw")

    elif action_type == "scroll":
        coord = action_input.get("coordinate")
        direction = action_input.get("direction", "down")
        if direction == "up":
            return await action_fn("scroll_up")
        return await action_fn("scroll_down")

    else:
        raise ValueError(f"Unknown computer_use action: {action_type}")

    return {}
