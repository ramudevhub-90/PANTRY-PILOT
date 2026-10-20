"""Meal ideas from Amazon Nova, built around what's in the pantry and about to expire.

Nova invents the dishes; this module only shapes the request and checks the answer against the
real pantry (which items it uses, which of those expire soon, what's missing). If Nova is
unavailable, the caller falls back to the fixed recipes in recipes.py.
"""

from __future__ import annotations

import re
from typing import Any

from .models import FoodPreferences, Meal, MealTimer, PantryItem

SUGGEST_PROMPT = """You are a home cook planning {occasion} from this household's pantry.
Suggest {count} different dishes that suit {occasion} and are commonly cooked at home where the
household lives (their time zone is {zone}), unless they prefer other cuisines. Strictly follow their
diet and never use ingredients they avoid. Use up food that expires soonest first, prefer dishes that
need nothing or very little that isn't in the pantry, and assume basic staples (salt, water, cooking
oil, common spices for the cuisine) are available. Keep steps short and practical: 3 to 6 steps.
Report the dishes with the suggest_meals tool."""

DIET_RULES = {
    "vegetarian": "vegetarian: no meat, poultry, fish or eggs (dairy is fine)",
    "eggetarian": "vegetarian plus eggs: no meat, poultry or fish",
    "vegan": "vegan: no animal products at all, including dairy, eggs and honey",
    "jain": "Jain: vegetarian, no eggs, and no onion, garlic, potatoes or other root vegetables",
    "non-vegetarian": "non-vegetarian: meat, fish and eggs are all welcome",
}

SUGGEST_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "suggest_meals",
        "description": "Report the suggested dishes.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "meals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "emoji": {"type": "string", "description": "One food emoji."},
                                "minutes": {"type": "integer", "description": "Total time."},
                                "servings": {"type": "integer"},
                                "uses": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Pantry item names used, exactly as listed.",
                                },
                                "missing": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Ingredients needed that are not in the pantry (not staples).",
                                },
                                "steps": {"type": "array", "items": {"type": "string"}},
                                "timer_label": {"type": "string", "description": "What a kitchen timer is for, e.g. rice."},
                                "timer_minutes": {"type": "integer"},
                            },
                            "required": ["name", "emoji", "minutes", "servings", "uses", "missing", "steps"],
                        },
                    }
                },
                "required": ["meals"],
            }
        },
    }
}


def suggest_request(
    inventory: list[PantryItem],
    occasion: str,
    count: int,
    wish: str | None,
    avoid: list[str],
    preferences: FoodPreferences | None = None,
    zone: str = "Asia/Kolkata",
) -> tuple[str, str]:
    """The system prompt and user message for Nova."""
    preferences = preferences or FoodPreferences()
    lines = [
        f"- {i.name}: {i.quantity:g} {i.unit}, " + ("expired" if i.days_left < 0 else f"expires in {i.days_left} days")
        for i in sorted(inventory, key=lambda i: i.days_left)
        if i.days_left >= 0 and not avoided(i.name, preferences)
    ]
    text ="Pantry:\n" + ("\n".join(lines) or "- (empty)")
    if preferences.diet in DIET_RULES:
        text += f"\n\nDiet: {DIET_RULES[preferences.diet]}."
    if preferences.cuisines:
        text += "\nFavourite cuisines: " + ", ".join(preferences.cuisines)
    if preferences.avoid:
        text += "\nNever use: " + ", ".join(preferences.avoid)
    if wish:
        text += f"\n\nThe user asked for: {wish}"
    if avoid:
        text += "\n\nAlready suggested, so pick different dishes: " + ", ".join(avoid)
    return SUGGEST_PROMPT.format(occasion=occasion, count=count, zone=zone), text


def avoided(name: str, preferences: FoodPreferences) -> bool:
    """The item contains an ingredient the household never wants."""
    lower = name.lower()
    return any(a.lower() in lower for a in preferences.avoid if a.strip())


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "meal"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _pantry_item(inventory: list[PantryItem], name: str) -> PantryItem | None:
    key = name.strip().lower()
    if not key:
        return None
    return next((i for i in inventory if i.name.lower() == key), None) or next(
        (i for i in inventory if key in i.name.lower() or i.name.lower() in key), None
    )


def to_meals(
    raw: list[dict[str, Any]], inventory: list[PantryItem], limit: int, preferences: FoodPreferences | None = None
) -> list[Meal]:
    """Nova's dishes as Meals, with `uses` and `rescues` checked against the real pantry. Dishes
    that use an ingredient the household avoids are dropped."""
    preferences = preferences or FoodPreferences()
    meals: list[Meal] = []
    seen: set[str] = set()
    for dish in raw:
        name = str(dish.get("name", "")).strip()
        steps = [str(s).strip() for s in dish.get("steps") or [] if str(s).strip()][:6]
        if not name or not steps or _slug(name) in seen:
            continue
        mentioned = [name, *map(str, dish.get("uses") or []), *map(str, dish.get("missing") or [])]
        if any(avoided(m, preferences) for m in mentioned):
            continue
        uses: list[PantryItem] = []
        for wanted in dish.get("uses") or []:
            item = _pantry_item(inventory, str(wanted))
            if item and item.days_left >= 0 and item not in uses:
                uses.append(item)
        rescues = [i.name for i in uses if i.days_left <= 3]
        missing = [str(m).strip() for m in dish.get("missing") or [] if str(m).strip() and not _pantry_item(inventory, str(m))]
        if rescues:
            verb = "they expire" if len(rescues) > 1 else "it expires"
            reason = f"Uses {_join([r.lower() for r in rescues])} before {verb}"
        else:
            reason = "Mostly from what you already have" if missing else "You have everything for this"
        timer_minutes = int(dish.get("timer_minutes") or 0)
        timer = (
            MealTimer(label=str(dish.get("timer_label") or name).strip()[:40], minutes=min(timer_minutes, 240))
            if timer_minutes > 0
            else None
        )
        seen.add(_slug(name))
        meals.append(
            Meal(
                id=_slug(name),
                name=name[:80],
                emoji=str(dish.get("emoji") or "🍽️")[:4],
                minutes=max(1, min(int(dish.get("minutes") or 20), 600)),
                servings=max(1, min(int(dish.get("servings") or 2), 20)),
                uses=[i.name for i in uses],
                rescues=rescues,
                missing=missing[:8],
                steps=steps,
                timer=timer,
                reason=reason,
            )
        )
        if len(meals) == limit:
            break
    return meals


_MEAT = ("chicken", "mutton", "lamb", "beef", "pork", "fish", "prawn", "shrimp", "meat", "bacon", "ham", "sausage")
_EGG = ("egg",)
_ANIMAL = (*_MEAT, *_EGG, "milk", "yogurt", "curd", "paneer", "cheese", "cheddar", "parmesan", "butter", "ghee", "cream", "honey")
_ROOTS = ("onion", "garlic", "potato", "carrot", "beetroot", "radish", "ginger")
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "vegetarian": (*_MEAT, *_EGG),
    "eggetarian": _MEAT,
    "vegan": _ANIMAL,
    "jain": (*_MEAT, *_EGG, *_ROOTS),
}


def suits(meal: Meal, preferences: FoodPreferences) -> bool:
    """For the built-in recipes: no ingredient the diet or the household rules out."""
    names = [meal.name, *meal.uses, *meal.missing]
    banned = _FORBIDDEN.get(preferences.diet, ())
    return not any(b in n.lower() for n in names for b in banned) and not any(avoided(n, preferences) for n in names)
