"""The conversational agent: Amazon Nova decides which Pantry Pilot MCP tools to call."""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from typing import Any

import anyio
from mcp import Client

from .mealtime import local_now, occasion_at
from .models import new_id
from .nova import Nova

SYSTEM_PROMPT = """You are Pantry Pilot, a friendly kitchen assistant that people talk to through Alexa+.
It is {now} for the user (their local time), so the next meal is {occasion}.

How to work:
- Use the tools for every fact about the kitchen. Never guess what's in stock.
- "What can I make?": call plan_meals (pass meal when the user names one, and request for wishes
  like vegetarian or quick), then recommend the top dish, say which expiring food it uses, and offer
  to start its timer or add missing items. For "something else" or "another idea", call plan_meals
  again; it suggests different dishes.
- When the user says they bought, used, finished or threw away food, call update_inventory with
  sensible quantities and units (add / use / remove). Assume typical pack sizes if none are given.
- When the user says what they eat ("we're vegetarian", "we like South Indian food", "no
  mushrooms"), call set_food_preferences; meal ideas follow it from then on.
- When the user agrees to an offer you made ("yes", "sure"), do it.
- Never place orders, check out or pay for anything.

How to reply (your words are spoken aloud):
- At most two short sentences. No lists, markdown or emoji.
- End with one short follow-up question only when it's genuinely useful."""

# Scanning needs media the chat channel doesn't carry, so the web app calls it directly.
_HIDDEN_TOOLS = {"scan_pantry"}
_THINKING = re.compile(r"<thinking>.*?</thinking>", re.S)


def tool_summary(result: Any) -> tuple[dict[str, Any], str]:
    """Pull the JSON payload and one-line summary out of a CallToolResult."""
    if result.structured_content is not None:
        payload = result.structured_content
    else:
        text = " ".join(getattr(block, "text", "") for block in result.content).strip()
        payload = {"summary": text or "Done"}
    return payload, str(payload.get("summary", "Done"))


async def call_tool_with_events(
    client: Client, name: str, args: dict[str, Any], display_args: dict[str, Any] | None = None
) -> AsyncIterator[dict[str, Any]]:
    """Call one MCP tool, yielding tool_start/tool_end events for the UI's tool trace.
    The final event carries the payload under "result"."""
    call_id = new_id()
    yield {"type": "tool_start", "id": call_id, "name": name, "args": display_args if display_args is not None else args}
    started = time.perf_counter()
    result = await client.call_tool(name, args)
    payload, summary = tool_summary(result)
    yield {
        "type": "tool_end",
        "id": call_id,
        "name": name,
        "ok": not result.is_error,
        "summary": summary,
        "durationMs": round((time.perf_counter() - started) * 1000),
        "result": payload,
    }


async def run_agent(
    text: str,
    history: list[dict[str, Any]],
    client: Client,
    nova: Nova,
    max_steps: int = 6,
) -> AsyncIterator[dict[str, Any]]:
    """One user turn: loop Nova -> MCP tools until Nova answers in words."""
    listed = await client.list_tools()
    tools = [
        {"toolSpec": {"name": t.name, "description": t.description or "", "inputSchema": {"json": t.input_schema}}}
        for t in listed.tools
        if t.name not in _HIDDEN_TOOLS
    ]
    now = local_now()
    system = SYSTEM_PROMPT.format(now=now.strftime("%A %d %B %Y, %I:%M %p"), occasion=occasion_at(now))
    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": [{"text": text}]}]

    for _ in range(max_steps):
        response = await anyio.to_thread.run_sync(lambda: nova.converse(system=system, messages=messages, tools=tools))
        message = response["output"]["message"]
        messages.append(message)
        uses = [block["toolUse"] for block in message["content"] if "toolUse" in block]

        if not uses:
            reply = _THINKING.sub("", " ".join(b["text"] for b in message["content"] if "text" in b)).strip()
            history.extend([messages[len(history)], {"role": "assistant", "content": [{"text": reply}]}])
            del history[:-12]  # keep the last six exchanges
            yield {"type": "reply", "text": reply}
            return

        results = []
        for use in uses:
            payload: dict[str, Any] = {}
            ok = True
            async for event in call_tool_with_events(client, use["name"], use.get("input") or {}):
                if event["type"] == "tool_end":
                    payload, ok = event["result"], event["ok"]
                yield event
            results.append(
                {"toolResult": {"toolUseId": use["toolUseId"], "content": [{"json": payload}], **({} if ok else {"status": "error"})}}
            )
        messages.append({"role": "user", "content": results})

    yield {"type": "reply", "text": "Sorry, that took more steps than I expected. Could you ask again more simply?"}
