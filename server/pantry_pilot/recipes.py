"""Recipe catalogue and the ranking that favours food about to expire."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Meal, MealTimer, PantryItem


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    emoji: str
    minutes: int
    servings: int
    # (key, label): key is matched as a substring of pantry item names.
    ingredients: tuple[tuple[str, str], ...]
    steps: tuple[str, ...]
    timer: MealTimer | None = None


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        "rice-bowl", "Chicken & Spinach Rice Bowl", "🍛", 30, 2,
        (("chicken", "Chicken thighs"), ("spinach", "Spinach"), ("rice", "Rice"),
         ("garlic", "Garlic"), ("lime", "Lime"), ("cilantro", "Cilantro")),
        ("Rinse the rice and simmer it, covered, for 15 minutes.",
         "Season the chicken and sear with garlic, 6–7 minutes a side.",
         "Wilt the spinach in the same pan for 1 minute.",
         "Slice the chicken and serve over rice with lime and cilantro."),
        MealTimer(label="rice", minutes=15),
    ),
    Recipe(
        "fajita-tacos", "Chicken Fajita Tacos", "🌮", 25, 3,
        (("chicken", "Chicken thighs"), ("pepper", "Bell peppers"), ("onion", "Onion"),
         ("tortilla", "Tortillas"), ("lime", "Lime"), ("cilantro", "Cilantro"),
         ("yogurt", "Greek yogurt"), ("cumin", "Ground cumin")),
        ("Slice chicken, peppers and onion into strips.",
         "Toss the chicken with cumin, lime juice and salt.",
         "Cook everything in a hot pan for 12 minutes.",
         "Serve in warm tortillas with yogurt and cilantro."),
        MealTimer(label="fajitas", minutes=12),
    ),
    Recipe(
        "frittata", "Spinach & Pepper Frittata", "🍳", 20, 2,
        (("egg", "Eggs"), ("spinach", "Spinach"), ("pepper", "Bell peppers"),
         ("cheddar", "Cheddar"), ("onion", "Onion")),
        ("Soften the onion and peppers in an oven-safe pan.",
         "Add spinach, then pour over the beaten eggs.",
         "Top with cheddar and bake at 200°C for 12 minutes."),
        MealTimer(label="frittata", minutes=12),
    ),
    Recipe(
        "tomato-penne", "Garlicky Tomato Penne", "🍝", 20, 2,
        (("penne", "Penne"), ("tomato", "Crushed tomatoes"), ("garlic", "Garlic"),
         ("onion", "Onion"), ("spinach", "Spinach"), ("olive oil", "Olive oil"),
         ("parmesan", "Parmesan")),
        ("Boil the penne for 10 minutes.",
         "Fry garlic and onion in olive oil, add tomatoes and simmer.",
         "Stir in spinach and the drained pasta.",
         "Finish with grated parmesan."),
        MealTimer(label="pasta", minutes=10),
    ),
    Recipe(
        "fried-rice", "Veggie Fried Rice", "🥡", 20, 2,
        (("rice", "Rice"), ("egg", "Eggs"), ("pepper", "Bell peppers"), ("onion", "Onion"),
         ("garlic", "Garlic"), ("soy sauce", "Soy sauce"), ("peas", "Frozen peas")),
        ("Scramble the eggs and set aside.",
         "Stir-fry onion, garlic, peppers and peas for 4 minutes.",
         "Add cold rice and soy sauce, then fold the eggs back in."),
        MealTimer(label="stir-fry", minutes=8),
    ),
    Recipe(
        "paneer-bhurji", "Paneer Bhurji with Rice", "🥘", 25, 2,
        (("paneer", "Paneer"), ("onion", "Onion"), ("tomato", "Tomatoes"),
         ("coriander", "Coriander"), ("garlic", "Garlic"), ("rice", "Rice")),
        ("Start the rice: simmer, covered, for 15 minutes.",
         "Fry onion and garlic until golden, then add chopped tomatoes.",
         "Crumble in the paneer and cook for 3 minutes with salt and spices.",
         "Finish with coriander and serve with the rice."),
        MealTimer(label="rice", minutes=15),
    ),
)


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def plan_meals(inventory: list[PantryItem], limit: int = 3) -> list[Meal]:
    scored = []
    for recipe in RECIPES:
        uses: list[str] = []
        rescues: list[str] = []
        missing: list[str] = []
        rescue_score = 0
        for key, label in recipe.ingredients:
            item = next((i for i in inventory if key in i.name.lower() and i.days_left >= 0), None)
            if item is None:
                missing.append(label)
                continue
            uses.append(item.name)
            if item.days_left <= 3:
                rescues.append(item.name)
                rescue_score += 3 if item.days_left <= 1 else 2
        # Favour recipes that use up expiring food, then ones we can cook without shopping.
        score = rescue_score * 2 + len(uses) - len(missing) * 3
        scored.append((score, recipe, uses, rescues, missing))

    scored.sort(key=lambda s: s[0], reverse=True)  # stable, so catalogue order breaks ties
    meals = []
    for _, recipe, uses, rescues, missing in scored[:limit]:
        if rescues:
            verb = "they expire" if len(rescues) > 1 else "it expires"
            reason = f"Uses {_join([r.lower() for r in rescues])} before {verb}"
        else:
            reason = "Mostly from what you already have" if missing else "You have everything for this"
        meals.append(
            Meal(
                id=recipe.id,
                name=recipe.name,
                emoji=recipe.emoji,
                minutes=recipe.minutes,
                servings=recipe.servings,
                uses=uses,
                rescues=rescues,
                missing=missing,
                steps=list(recipe.steps),
                timer=recipe.timer,
                reason=reason,
            )
        )
    return meals


def recipe_by_id(recipe_id: str) -> Recipe | None:
    return next((r for r in RECIPES if r.id == recipe_id), None)
