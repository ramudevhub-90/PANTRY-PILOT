"""Meal ideas from Amazon Nova, built around what's in the pantry and about to expire.

Nova invents the dishes; this module only shapes the request and checks the answer against the
real pantry (which items it uses, which of those expire soon, what's missing). If Nova is
unavailable, the caller falls back to the fixed recipes in recipes.py.
"""

from __future__ import annotations

import re
from typing import Any

from .models import FoodPreferences, Meal, MealTimer, PantryItem

SUGGEST_PROMPT = """You are a home cook in {place} planning {occasion} from this household's pantry.
Suggest {count} different dishes that people in {place} really cook at home for {occasion}{cuisine_note}.
{occasion_note}
Strictly follow the household's diet and never use ingredients they avoid. Every dish must really be
the dish its name says: its main ingredient must be in the pantry, and its steps must cook that dish
(never give a prawn dish's name to an egg recipe). Use up food that expires soonest first, but only
when it suits the dish and cuisine; leave out expiring items that don't fit. It is fine to need one or
two cheap extra ingredients, never the main one. Assume basic staples (salt,
water, cooking oil, common spices for the cuisine) are available. Never repeat a dish already
suggested. Give 3 to 6 short steps, one sentence each.
Report the dishes with the suggest_meals tool."""

OCCASION_NOTES = {
    "breakfast": "Breakfast dishes only, not lunch or dinner food.",
    "brunch": "Something filling but light, between breakfast and lunch.",
    "lunch": "A proper lunch.",
    "evening snacks": "Light tea-time snacks, not full meals: think finger food or small plates.",
    "dinner": "A proper dinner.",
    "late-night snack": "Quick, light bites that take under 15 minutes.",
}

# Time zones for the countries we mostly serve; others go to Nova as the zone name, which it can place.
_PLACES = {
    "Asia/Kolkata": "India",
    "Asia/Calcutta": "India",
    "Asia/Dubai": "the UAE",
    "Asia/Singapore": "Singapore",
    "Europe/London": "the UK",
}


def place_for(zone: str) -> str:
    if zone in _PLACES:
        return _PLACES[zone]
    if zone.startswith(("America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Phoenix")):
        return "the United States"
    return f"the region of time zone {zone}"


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
                                "plain_name": {
                                    "type": "string",
                                    "description": "A plain English description of what these steps make, e.g. Bengali spinach and onion stir-fry.",
                                },
                                "name_matches_recipe": {
                                    "type": "boolean",
                                    "description": "True only if `name` is the real, traditional name of exactly the dish these steps cook.",
                                },
                                "main_ingredient": {
                                    "type": "string",
                                    "description": "The ingredient the dish is named for (prawns for chingri malai, lentils for dal).",
                                },
                                "timer_label": {"type": "string", "description": "What a kitchen timer is for, e.g. rice."},
                                "timer_minutes": {"type": "integer"},
                            },
                            "required": ["name", "plain_name", "name_matches_recipe", "emoji", "minutes", "servings", "main_ingredient", "uses", "missing", "steps"],
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
    home = preferences.home_cuisine.strip()
    if preferences.cuisines and home and home.lower() != "local":
        # Both: mostly the food they grew up with, sometimes their other favourites.
        cuisine_note = (
            f". They grew up eating {home} food: make most dishes {home} home cooking, and at most one "
            f"from their other favourites ({', '.join(preferences.cuisines)}), using ingredients easy to "
            "buy where they live"
        )
    elif preferences.cuisines:
        cuisine_note = f", in these cuisines: {', '.join(preferences.cuisines)}"
    elif home and home.lower() != "local":
        # Migrants: cook the food they grew up with, from what the local shops sell.
        cuisine_note = (
            f". They grew up eating {home} food: mostly suggest {home} home cooking, with an "
            "occasional local favourite, using ingredients easy to buy where they live"
        )
    if home and home.lower() != "local":
        cuisine_note += (
            f". Decide each real, traditional {home} dish first, then write that dish's actual recipe; use its usual name (e.g. the dish a {home} "
            "family would actually call it), with a short English description in brackets. Never invent "
            f"\"{home}-Style <foreign dish>\" fusions such as pasta or stir-fries; skip pantry items that "
            "don't belong in that cuisine and pick the expiring ones that do"
        )
    elif preferences.location.strip():
        cuisine_note = ", in the home-style cuisine of that city and state"
    else:
        cuisine_note = ", in the local home-style cuisine"
    if preferences.language.strip().lower() not in ("", "english", "auto"):
        cuisine_note += (
            f". Write each dish name, its steps and the timer label in {preferences.language}, with the "
            "English dish name in brackets after the name; keep `uses` and `missing` exactly as the pantry "
            "lists them"
        )
    system = SUGGEST_PROMPT.format(
        place=preferences.location.strip() or place_for(zone),
        occasion=occasion,
        count=count,
        cuisine_note=cuisine_note,
        occasion_note=OCCASION_NOTES.get(occasion, ""),
    )
    return system, text


def avoided(name: str, preferences: FoodPreferences) -> bool:
    """The item contains an ingredient the household never wants."""
    lower = name.lower()
    return any(a.lower() in lower for a in preferences.avoid if a.strip())


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "meal"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _mentions(steps: list[str], ingredient: str) -> bool:
    """Some word of the ingredient appears in the steps (so "red lentils" counts for "add the lentils")."""
    words = _words(ingredient) - _GENERIC or _words(ingredient)
    return bool(words & _words(" ".join(steps)))


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


_GENERIC = {"fresh", "red", "green", "white", "black", "cheese", "oil", "powder", "leave", "baby", "whole", "dried"}


def _pantry_item(inventory: list[PantryItem], name: str) -> PantryItem | None:
    """The pantry item this ingredient refers to: same name, one name inside the other, or a shared
    specific word ("cheddar cheese" is the Cheddar on the shelf; "red lentils" are Masoor dal's lentils)."""
    key = name.strip().lower()
    if not key:
        return None
    exact = next((i for i in inventory if i.name.lower() == key), None)
    if exact:
        return exact
    contained = next((i for i in inventory if key in i.name.lower() or i.name.lower() in key), None)
    if contained:
        return contained
    wanted = _words(key) - _GENERIC
    return next((i for i in inventory if wanted & (_words(i.name) - _GENERIC)), None)


def to_meals(
    raw: list[dict[str, Any]],
    inventory: list[PantryItem],
    limit: int,
    preferences: FoodPreferences | None = None,
    exclude: list[str] | None = None,
) -> list[Meal]:
    """Nova's dishes as Meals, with `uses` and `rescues` checked against the real pantry. Dishes
    that use an ingredient the household avoids, or were already suggested (`exclude`), are dropped."""
    preferences = preferences or FoodPreferences()
    meals: list[Meal] = []
    seen: set[str] = {_slug(n) for n in exclude or []}
    for dish in raw:
        name = str(dish.get("name", "")).strip()
        plain = str(dish.get("plain_name") or "").strip()
        if plain and dish.get("name_matches_recipe") is False:
            name = plain  # a traditional name on a different recipe misleads; describe it instead
        steps = [str(s).strip() for s in dish.get("steps") or [] if str(s).strip()][:6]
        if not name or not steps or _slug(name) in seen:
            continue
        mentioned = [name, *map(str, dish.get("uses") or []), *map(str, dish.get("missing") or [])]
        if any(avoided(m, preferences) for m in mentioned):
            continue
        # The dish must be what its name says: its main ingredient is in stock and in its steps.
        # Reject only what is clearly wrong: the dish's own main ingredient is listed as missing
        # (a "Chingri Malai" without prawns), or the dish uses nothing at all from the pantry.
        main = str(dish.get("main_ingredient") or "").strip()
        missing_raw = [str(m) for m in dish.get("missing") or []]
        main_words = _words(main) - _GENERIC or _words(main)
        if main and any(main_words & _words(m) for m in missing_raw):
            continue
        if main and not _pantry_item(inventory, main) and not any(_pantry_item(inventory, str(u)) for u in dish.get("uses") or []):
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
                emoji=dish_emoji(name, str(dish.get("emoji") or "")),
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


# A meat emoji must match the dish: Nova once gave an egg scramble 🥓.
_MEAT_EMOJI = {
    "🥓": ("bacon", "pork", "ham"),
    "🍗": ("chicken",),
    "🍖": ("mutton", "lamb", "meat", "rib", "pork"),
    "🥩": ("steak", "beef", "mutton", "meat", "lamb"),
    "🐟": ("fish", "macher", "meen"),
    "🍤": ("prawn", "shrimp", "chingri"),
    "🦀": ("crab",),
}
_DISH_EMOJI = (
    (("egg", "omelette", "anda", "dim"), "🍳"),
    (("dal", "sambar", "rasam", "soup", "stew", "curry", "jhol", "korma"), "🍛"),
    (("rice", "pulao", "biryani", "khichdi", "bhath", "bath"), "🍚"),
    (("roti", "paratha", "chapati", "dosa", "uttapam", "pancake", "chilla"), "🫓"),
    (("salad", "raita", "chaat"), "🥗"),
    (("noodle", "pasta", "maggi"), "🍜"),
    (("tea", "chai"), "🍵"),
)


def dish_emoji(name: str, emoji: str) -> str:
    """Nova's emoji for the dish, unless it shows meat the dish doesn't have; then one from the name."""
    lower = name.lower()
    emoji = emoji.strip()[:4]
    if emoji and not (emoji in _MEAT_EMOJI and not any(w in lower for w in _MEAT_EMOJI[emoji])):
        return emoji
    return next((e for words, e in _DISH_EMOJI if any(w in lower for w in words)), "🍽️")
