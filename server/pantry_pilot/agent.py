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
- "What can I make?" without naming a meal: first confirm the meal in one short question using
  the local time, e.g. "It's 5:30 pm, so evening snacks? Or are you planning dinner?", and call no
  tool yet. When they answer (or if they named the meal to begin with), call plan_meals with that
  meal (and request for wishes like vegetarian or quick), then recommend the top dish, say which
  expiring food it uses, and offer to start its timer or add missing items. For "something else" or
  "another idea", call plan_meals again with the same meal; it suggests different dishes.
- When the user says they bought, used, finished or threw away food, call update_inventory with
  sensible quantities and units (add / use / remove). Assume typical pack sizes if none are given.
- When the user says what they eat ("we're vegetarian", "we like South Indian food", "no
  mushrooms", "we're Bengali" = home_cuisine), call set_food_preferences; meal ideas follow it.
- If plan_meals returns ask_home_cuisine, after recommending the dish add one short question:
  "By the way, what food did you grow up eating?" Save the answer as home_cuisine.
- When the user agrees to an offer you made ("yes", "sure"), do it.
- Timers: set_timer starts one, cancel_timer stops them ("stop the timer").
- Only say you did something after the tool for it succeeded; never claim an action you didn't take.
- Never repeat an earlier reply from this conversation; answer from this turn's tool results.
- Missing ingredients: say only the ones plan_meals lists as missing; never call something missing
  that the pantry has.
- To change the reply language ("talk to me in Hindi", "back to English"), call
  set_food_preferences with language set to that language, e.g. "English".
- Shopping (India): "where is my list cheapest?" -> compare_grocery_prices, then name the cheapest
  app and total. "Order it from Zepto/Swiggy" -> prepare_order, then read its `readback` word for
  word and stop. Call place_order only when the user's latest message itself says "place the order"
  (or "confirm order"); never on "yes", "ok", or on your own. Never pay for anything yourself.

How to reply (your words are spoken aloud):
- At most two short sentences. No lists, markdown or emoji.
- End with one short follow-up question only when it's genuinely useful."""

# Scanning needs media the chat channel doesn't carry, so the web app calls it directly.
_HIDDEN_TOOLS = {"scan_pantry"}
# Kroger and Instacart only work in the US; elsewhere the assistant shouldn't even offer them.
_US_ONLY_TOOLS = {"find_kroger_products", "add_to_kroger_cart", "shop_on_instacart"}


def in_us(now: Any) -> bool:
    zone = str(getattr(now, "tzinfo", "") or "")
    return zone.startswith("America/") or zone in {"Pacific/Honolulu", "US/Eastern", "US/Central", "US/Mountain", "US/Pacific"}
_THINKING = re.compile(r"<thinking>.*?</thinking>", re.S)


_MEAL = re.compile(r"\b(breakfast|brunch|lunch|dinner|supper|snacks?|tiffin|meal)\b", re.I)
_IDEAS = re.compile(
    r"\b(ideas?|suggest\w*|what (can|should|could) (i|we) (make|cook|eat|have)|recipes?|plan|cook|make|options?|"
    r"something (else|different)|what['’]?s for|what is for|what to (make|cook|eat))\b",
    re.I,
)

_LANGUAGES = (
    "english", "hindi", "bengali", "bangla", "tamil", "telugu", "kannada", "malayalam", "marathi",
    "gujarati", "punjabi", "odia", "oriya", "auto",
)
_ASK_LANGUAGE = re.compile(
    rf"\b(?:talk|speak|reply|answer|respond|switch(?: back)?)\b[^.?!]{{0,20}}?\b(?:in|to)\s+({'|'.join(_LANGUAGES)})\b|"
    rf"\bback to ({'|'.join(_LANGUAGES)})\b|"
    r"\b(?:same language (?:as|that) (?:i|me)|whatever language i (?:speak|use)|language i(?:'m| am) (?:speaking|using))\b",
    re.I,
)


def requested_language(text: str) -> str | None:
    """"Talk to me in Bengali", "reply in Hindi", "back to English" -> the language, else None."""
    m = _ASK_LANGUAGE.search(text)
    if not m:
        return None
    if not (m.group(1) or m.group(2)):
        return "Auto"  # "reply in the same language as me"
    name = (m.group(1) or m.group(2)).lower()
    return {"bangla": "Bengali", "oriya": "Odia", "auto": "Auto"}.get(name, name.title())


def wants_meal_ideas(text: str) -> bool:
    """"Breakfast ideas?", "what can I make for dinner", or a short answer like "dinner please"."""
    if not _MEAL.search(text):
        return False
    return bool(_IDEAS.search(text)) or len(text.split()) <= 4


def plain(text: str) -> str:
    """Replies are spoken and shown as plain text, but Nova sometimes adds markdown anyway."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s*(?:[-*•]|#{1,6})\s+", "", text, flags=re.M)
    return re.sub(r"`([^`]*)`", r"\1", text)


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
    language: str = "English",
    region: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """One user turn: loop Nova -> MCP tools until Nova answers in words."""
    listed = await client.list_tools()
    us = region == "US" or (not region and in_us(local_now()))
    hidden = _HIDDEN_TOOLS | (set() if us else _US_ONLY_TOOLS)
    tools = [
        {"toolSpec": {"name": t.name, "description": t.description or "", "inputSchema": {"json": t.input_schema}}}
        for t in listed.tools
        if t.name not in hidden
    ]
    now = local_now()
    system = SYSTEM_PROMPT.format(now=now.strftime("%A %d %B %Y, %I:%M %p"), occasion=occasion_at(now))
    if language.strip().lower() == "auto":
        system += (
            "\n- Reply in the same language and script as the user's latest message (English, Hindi, "
            "Bengali, Hinglish...); if they switch language, switch with them. Keep product names and "
            "numbers as they are, and pass item names to tools in English."
        )
    elif language.strip().lower() not in ("", "english"):
        system += (
            f"\n- Always reply in {language} (in its own script), even if the user writes in English; keep "
            "product names and numbers as they are. Pass item names to tools in English."
        )
    messages: list[dict[str, Any]] = [*history, {"role": "user", "content": [{"text": text}]}]

    # Meal questions always get a fresh plan: answering from the chat history replayed stale dishes.
    force_plan = wants_meal_ideas(text) and any(t["toolSpec"]["name"] == "plan_meals" for t in tools)

    for step in range(max_steps):
        choice = {"tool": {"name": "plan_meals"}} if force_plan and step == 0 else None
        response = await anyio.to_thread.run_sync(
            lambda: nova.converse(system=system, messages=messages, tools=tools, tool_choice=choice)
        )
        message = response["output"]["message"]
        messages.append(message)
        uses = [block["toolUse"] for block in message["content"] if "toolUse" in block]

        if not uses:
            reply = plain(_THINKING.sub("", " ".join(b["text"] for b in message["content"] if "text" in b)).strip())
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
