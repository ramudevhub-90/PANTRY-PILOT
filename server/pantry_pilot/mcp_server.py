"""The Pantry Pilot MCP server: the tools Alexa+ (and our own agent) call."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations
from pydantic import BaseModel

from .meal_ideas import suggest_request, suits, to_meals
from .mealtime import current_occasion, local_now
from .models import Category, Diet, GroceryItem, Household, KitchenTimer, PantryItem
from .nova import Nova, parse_data_url
from .recipes import RECIPES, plan_meals
from .store import Store

log = logging.getLogger(__name__)

INSTRUCTIONS = """Pantry Pilot keeps track of a household's food: what's in stock, what expires soon,
what to cook, the grocery list and kitchen timers.
- Call get_inventory before answering questions about what's in the kitchen.
- For "what can I make", call plan_meals; it favours food that is about to expire and picks the
  meal (breakfast, lunch, dinner, snacks...) from the user's local time unless one is named.
- When the user states a diet, favourite cuisines or foods to avoid, call set_food_preferences.
- When the user buys, uses or finishes food, call update_inventory.
- scan_pantry only reads photos, videos or bills and returns detections; save them with
  update_inventory(action="add") after the user confirms."""

CONFIDENT = 0.85
"""Detections at or above this confidence don't need the user to double-check them."""

_DEFAULT_SHELF_LIFE: dict[str, int] = {
    "produce": 5, "dairy": 7, "protein": 3, "grains": 180, "pantry": 180, "condiments": 365,
}

# Units that convert into each other: unit -> (base unit, factor).
_CONVERTIBLE: dict[str, tuple[str, float]] = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "L": ("ml", 1000)}


class StockChange(BaseModel):
    name: str
    quantity: float | None = None
    unit: str | None = None
    expires_in_days: int | None = None
    emoji: str | None = None
    category: Category | None = None


def _convert(quantity: float, unit: str, target: str) -> float | None:
    """Convert between g/kg or ml/L; None when the units aren't compatible."""
    if unit == target:
        return quantity
    a, b = _CONVERTIBLE.get(unit), _CONVERTIBLE.get(target)
    if a and b and a[0] == b[0]:
        return quantity * a[1] / b[1]
    return None


def _singular(name: str) -> str:
    return name.strip().lower().rstrip("s")


def _match(household: Household, name: str, loose: bool) -> PantryItem | None:
    """Same product, ignoring case and plurals. With loose=True, also a partial match
    ("spinach" finds "Baby spinach"), which suits "we finished the spinach" but not adding
    new stock, where "Tomatoes" must not merge into "Crushed tomatoes"."""
    key = _singular(name)
    if same := next((i for i in household.inventory if _singular(i.name) == key), None):
        return same
    if not loose or not key:
        return None
    return next((i for i in household.inventory if key in i.name.lower() or _singular(i.name) in key), None)


def _fmt_qty(quantity: float, unit: str) -> str:
    return f"{quantity:g} {unit}"


def _join(names: list[str]) -> str:
    if not names:
        return "nothing"
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _item_view(item: PantryItem) -> dict[str, Any]:
    return {
        "name": item.name,
        "quantity": item.quantity,
        "unit": item.unit,
        "category": item.category,
        "days_left": item.days_left,
    }


def create_server(store: Store, nova: Nova, household_id: Callable[[], str]) -> MCPServer:
    """`household_id` is called on every tool call, so each signed-in user gets their own pantry."""
    mcp = MCPServer(
        name="pantry-pilot",
        title="Pantry Pilot",
        description="A kitchen agent that plans meals around what's about to expire.",
        instructions=INSTRUCTIONS,
        version="0.1.0",
    )

    def load() -> Household:
        return store.load(household_id())

    def save(household: Household) -> None:
        store.save(household_id(), household)

    @mcp.tool(
        title="Read the pantry",
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def get_inventory(filter: Literal["all", "expiring"] = "all") -> dict[str, Any]:
        """List pantry items with quantities and days until they expire, soonest first.
        Use filter="expiring" for items that expire within 3 days."""
        items = sorted(load().inventory, key=lambda i: i.days_left)
        expiring = [i for i in items if i.days_left <= 3]
        if filter == "expiring":
            return {"summary": f"{len(expiring)} items expire within 3 days", "items": [_item_view(i) for i in expiring]}
        return {
            "summary": f"{len(items)} items · {len(expiring)} expiring soon",
            "items": [_item_view(i) for i in items],
        }

    @mcp.tool(title="Update the pantry", annotations=ToolAnnotations(destructive_hint=True))
    def update_inventory(
        action: Literal["add", "use", "remove", "set"],
        items: list[StockChange],
        source: Literal["voice", "scan", "manual", "order"] = "voice",
    ) -> dict[str, Any]:
        """Change pantry stock.
        - add: groceries bought or confirmed from a scan; quantities add to existing stock.
        - use: some was used in cooking; subtracts the quantity (removes it when none is left).
        - remove: the item is finished or thrown away.
        - set: correct an item's quantity, unit or expiry.
        Give quantity and unit for add, use and set (e.g. 500 g, 2 pcs, 1 L).
        source: where new items came from; leave as "voice" for things the user said."""
        household = load()
        changed: list[str] = []
        not_found: list[str] = []
        today = date.today()

        for change in items:
            existing = _match(household, change.name, loose=action != "add")

            if action == "add":
                if change.quantity is None or not change.unit:
                    not_found.append(change.name)
                    continue
                category = change.category or (existing.category if existing else "pantry")
                days = change.expires_in_days if change.expires_in_days is not None else _DEFAULT_SHELF_LIFE[category]
                expires = today + timedelta(days=days)
                if existing and (qty := _convert(change.quantity, change.unit, existing.unit)) is not None:
                    existing.quantity = round(existing.quantity + qty, 2)
                    # Keep the older stock's date while it's still good, so it gets used up first.
                    if existing.days_left < 0 or expires < existing.expires_on:
                        existing.expires_on = expires
                elif existing:
                    existing.quantity, existing.unit, existing.expires_on = change.quantity, change.unit, expires
                else:
                    household.inventory.append(
                        PantryItem(
                            name=change.name.strip().capitalize(),
                            emoji=change.emoji or "🛒",
                            quantity=change.quantity,
                            unit=change.unit,
                            category=category,
                            expires_on=expires,
                            source=source,
                        )
                    )
                changed.append(change.name)

            elif existing is None:
                not_found.append(change.name)

            elif action == "remove" or (action == "use" and change.quantity is None):
                household.inventory.remove(existing)
                changed.append(existing.name)

            elif action == "use":
                qty = _convert(change.quantity, change.unit or existing.unit, existing.unit)
                if qty is None:
                    not_found.append(change.name)
                    continue
                existing.quantity = round(existing.quantity - qty, 2)
                if existing.quantity <= 0:
                    household.inventory.remove(existing)
                changed.append(existing.name)

            else:  # set
                if change.quantity is not None:
                    existing.quantity = change.quantity
                if change.unit:
                    existing.unit = change.unit
                if change.expires_in_days is not None:
                    existing.expires_on = today + timedelta(days=change.expires_in_days)
                changed.append(existing.name)

        save(household)
        verb = {"add": "Added", "use": "Used", "remove": "Removed", "set": "Updated"}[action]
        summary = f"{verb} {_join(changed)}" + (f" · couldn't find {_join(not_found)}" if not_found else "")
        return {"summary": summary, "changed": changed, "not_found": not_found}

    @mcp.tool(title="Scan groceries or a bill", annotations=ToolAnnotations(read_only_hint=True))
    async def scan_pantry(
        images: list[str] | None = None,
        video: str | None = None,
        document: str | None = None,
        kind: Literal["auto", "groceries", "receipt", "order_screenshot"] = "auto",
    ) -> dict[str, Any]:
        """Identify grocery items in photos, a short video or a PDF bill using Amazon Nova.
        Media are base64 data URLs. Returns detections with quantities and confidence; it does
        NOT change the pantry. Save confirmed items with update_inventory(action="add")."""
        sources = [*(images or []), *([video] if video else []), *([document] if document else [])]
        if not sources:
            raise ValueError("Provide at least one image, a video or a document.")
        media = [parse_data_url(s) for s in sources]
        detected = await anyio.to_thread.run_sync(lambda: nova.detect_items(media, kind))
        unsure = sum(1 for d in detected if d.confidence < CONFIDENT)
        return {
            "summary": f"Detected {len(detected)} items" + (f" · {unsure} to check" if unsure else ""),
            "items": [d.model_dump() for d in detected],
            "unsure": unsure,
        }

    # The Python name avoids shadowing the imported planner; MCP clients see "plan_meals".
    @mcp.tool(name="plan_meals", title="Plan meals")
    async def plan_meals_tool(
        meal: Literal["breakfast", "brunch", "lunch", "evening snacks", "dinner", "late-night snack"] | None = None,
        request: str | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        """Suggest dishes that use food about to expire. `meal` defaults to the one due at the
        user's local time; pass it when the user names one ("plan lunch"). `request` carries wishes
        such as "vegetarian", "South Indian" or "under 20 minutes". Calling it again suggests
        different dishes. Saves the plan so the user sees it."""
        household = load()
        occasion = meal or current_occasion()
        count = max(1, min(limit, 5))
        avoid = [m.name for m in household.meals]
        prefs = household.preferences
        zone = str(local_now().tzinfo)
        # Ask for spares: Nova sometimes repeats a dish or picks one the diet rules out.
        system, text = suggest_request(household.inventory, occasion, count + 3, request, avoid, prefs, zone)
        try:
            raw = await anyio.to_thread.run_sync(nova.suggest_meals, system, text)
            meals = to_meals(raw, household.inventory, count, prefs, exclude=avoid)
        except Exception:
            log.warning("Nova meal ideas failed; using the built-in recipes", exc_info=True)
            meals = []
        else:
            if not meals:
                log.warning("Nova returned no usable meal ideas (%d raw)", len(raw))
        source = "Amazon Nova"
        if not meals:
            meals = [m for m in plan_meals(household.inventory, limit=len(RECIPES)) if suits(m, prefs)][:count]
            source = f"{len(RECIPES)} built-in recipes"
        for m in meals:
            m.occasion = occasion
        household.meals = meals
        save(household)
        ask_home = not prefs.home_cuisine and not prefs.cuisines
        return {
            # Not asked yet: the assistant asks once which food they grew up with.
            **({"ask_home_cuisine": True} if ask_home else {}),
            "summary": f"{occasion.capitalize()} ideas from {source} · "
            + (f"top pick: {meals[0].name}" if meals else "nothing fits right now"),
            "occasion": occasion,
            "meals": [
                {
                    "id": m.id,
                    "name": m.name,
                    "minutes": m.minutes,
                    "uses": m.uses,
                    "rescues": m.rescues,
                    "missing": m.missing,
                    "steps": m.steps,
                    "timer": m.timer.model_dump() if m.timer else None,
                    "reason": m.reason,
                }
                for m in meals
            ],
        }

    @mcp.tool(title="Set food preferences")
    def set_food_preferences(
        diet: Diet | None = None,
        cuisines: list[str] | None = None,
        avoid: list[str] | None = None,
        location: str | None = None,
        home_cuisine: str | None = None,
        language: str | None = None,
        region: Literal["IN", "US", "auto"] | None = None,
    ) -> dict[str, Any]:
        """Remember what the household eats, so meal ideas follow it: diet ("we're vegetarian",
        "we eat eggs but no meat" = eggetarian, "Jain"), favourite cuisines ("we like South Indian
        food"), ingredients to avoid (allergies, dislikes) and where they live ("we're in Pune" =
        "Pune, Maharashtra, India"), and the food they grew up with when it differs from where they
        live ("we're Bengali" in Bengaluru = home_cuisine "Bengali"; "local" = same as where they
        live), and the language to reply in ("talk to me in Bengali" = language "Bengali"; "reply in
        whatever language I speak" = "Auto"). Only the
        fields given change; pass an empty list to clear
        cuisines or avoid, or an empty location to go back to the time zone's country."""
        household = load()
        prefs = household.preferences
        if diet is not None:
            prefs.diet = diet
        if cuisines is not None:
            prefs.cuisines = [c.strip() for c in cuisines if c.strip()][:6]
        if avoid is not None:
            prefs.avoid = [a.strip() for a in avoid if a.strip()][:12]
        if location is not None:
            prefs.location = location.strip()[:80]
        if home_cuisine is not None:
            prefs.home_cuisine = home_cuisine.strip()[:40]
        if language is not None:
            name = language.strip().lower()
            if name in ("", "en", "eng", "english", "default"):
                prefs.language = "English"
            elif name in ("auto", "same", "same as i speak", "same as me", "any"):
                prefs.language = "Auto"
            else:
                prefs.language = name.title()[:24]
        if region is not None:
            prefs.region = "" if region == "auto" else region
        save(household)
        parts = [prefs.location] if prefs.location else []
        if prefs.home_cuisine and prefs.home_cuisine.lower() != "local":
            parts.append(f"{prefs.home_cuisine} home food")
        parts.append(prefs.diet if prefs.diet != "any" else "any diet")
        if prefs.language.lower() == "auto":
            parts.append("replies in the language you speak")
        elif prefs.language.lower() != "english":
            parts.append(f"replies in {prefs.language}")
        if prefs.cuisines:
            parts.append(", ".join(prefs.cuisines))
        if prefs.avoid:
            parts.append("no " + ", ".join(prefs.avoid))
        return {"summary": "Saved: " + " · ".join(parts), "preferences": prefs.model_dump(by_alias=True)}

    @mcp.tool(title="Add to the grocery list")
    def build_grocery_list(items: list[str] | None = None, meal_id: str | None = None) -> dict[str, Any]:
        """Add items to the grocery list. Pass item names, or a meal_id from plan_meals to add
        that meal's missing ingredients (defaults to the top planned meal). Expired pantry items
        are added too when building from a meal."""
        household = load()
        open_names = {g.name.lower() for g in household.grocery if not g.checked}
        wanted: list[GroceryItem] = []
        if items:
            wanted = [GroceryItem(name=n.strip().capitalize(), note="You asked", source="voice") for n in items if n.strip()]
        else:
            meal = next((m for m in household.meals if m.id == meal_id), household.meals[0] if household.meals else None)
            if meal:
                wanted += [GroceryItem(name=n, note=f"For {meal.name}") for n in meal.missing]
            wanted += [GroceryItem(name=i.name, note="Expired") for i in household.inventory if i.days_left < 0]
        added = []
        for g in wanted:
            if g.name.lower() not in open_names:
                household.grocery.append(g)
                open_names.add(g.name.lower())
                added.append(g.name)
        save(household)
        return {"summary": f"Added {_join(added)}" if added else "Nothing new to add", "added": added}

    @mcp.tool(title="Read the grocery list", annotations=ToolAnnotations(read_only_hint=True))
    def get_grocery_list() -> dict[str, Any]:
        """List the items still to buy."""
        open_items = [g.name for g in load().grocery if not g.checked]
        return {"summary": f"{len(open_items)} items to buy", "items": open_items}

    @mcp.tool(title="Set a kitchen timer")
    def set_timer(label: str, minutes: float) -> dict[str, Any]:
        """Start a kitchen timer, e.g. label="rice", minutes=15."""
        seconds = max(1, round(minutes * 60))
        household = load()
        household.timers.append(
            KitchenTimer(label=label.strip().lower() or "kitchen", total_seconds=seconds, ends_at=int(time.time() * 1000) + seconds * 1000)
        )
        save(household)
        return {"summary": f"{label} · {seconds // 60:02d}:{seconds % 60:02d}", "label": label, "seconds": seconds}

    @mcp.tool(title="Stop a kitchen timer")
    def cancel_timer(label: str | None = None) -> dict[str, Any]:
        """Stop running kitchen timers. Pass the timer's label ("rice") to stop just that one;
        without a label every running timer stops. Returns what was stopped and what still runs."""
        household = load()
        now_ms = int(time.time() * 1000)
        running = [t for t in household.timers if t.ends_at > now_ms]
        key = (label or "").strip().lower()
        stopped = [t for t in running if not key or key in t.label.lower() or t.label.lower() in key]
        household.timers = [t for t in running if t not in stopped]
        save(household)
        names = [t.label for t in stopped]
        left = [t.label for t in household.timers]
        if names:
            summary = "Stopped " + _join(names)
        else:
            summary = "No timer matched" if running else "No timers were running"
        return {"summary": summary, "stopped": names, "still_running": left}

    return mcp
