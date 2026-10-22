from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Category = Literal["produce", "dairy", "protein", "grains", "pantry", "condiments"]
ItemSource = Literal["seed", "scan", "voice", "manual", "order"]

UNITS = ("pcs", "g", "kg", "ml", "L", "bunch", "bag", "pack", "tub", "bottle", "can", "bulb")


def new_id() -> str:
    return uuid.uuid4().hex


def days_until(day: date, today: date | None = None) -> int:
    return (day - (today or date.today())).days


class ApiModel(BaseModel):
    """Serialises to camelCase for the web UI, accepts either case on input."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PantryItem(ApiModel):
    id: str = Field(default_factory=new_id)
    name: str
    emoji: str = "🛒"
    quantity: float
    unit: str
    category: Category = "pantry"
    expires_on: date
    source: ItemSource = "manual"

    @property
    def days_left(self) -> int:
        return days_until(self.expires_on)


class DetectedItem(ApiModel):
    """An item the vision model found, waiting for the user to confirm it."""

    id: str = Field(default_factory=new_id)
    name: str
    emoji: str = "🛒"
    quantity: float
    unit: str
    category: Category = "pantry"
    expires_in_days: int
    confidence: float = Field(ge=0, le=1)


class GroceryItem(ApiModel):
    id: str = Field(default_factory=new_id)
    name: str
    note: str | None = None
    checked: bool = False
    source: Literal["agent", "voice", "manual"] = "agent"


class KitchenTimer(ApiModel):
    id: str = Field(default_factory=new_id)
    label: str
    total_seconds: int
    ends_at: int
    """Epoch milliseconds, matching JavaScript's Date.now()."""


class MealTimer(ApiModel):
    label: str
    minutes: int


class Meal(ApiModel):
    id: str
    name: str
    emoji: str
    minutes: int
    servings: int
    uses: list[str]
    rescues: list[str]
    missing: list[str]
    steps: list[str]
    timer: MealTimer | None = None
    reason: str
    occasion: str | None = None
    """breakfast, lunch, dinner, evening snacks... (see mealtime.OCCASIONS)."""


Diet = Literal["any", "vegetarian", "eggetarian", "vegan", "jain", "non-vegetarian"]


class FoodPreferences(ApiModel):
    """What the household likes to eat; meal ideas follow it."""

    diet: Diet = "any"
    cuisines: list[str] = Field(default_factory=list)
    """e.g. South Indian, Bengali, Italian. Empty: dishes common where the user lives."""
    avoid: list[str] = Field(default_factory=list)
    """Ingredients never to use (allergies, dislikes)."""
    location: str = ""
    """City and region, e.g. "Bengaluru, Karnataka, India". Empty: the country from the time zone."""
    region: str = ""
    """Shopping apps to use: "IN" (Swiggy, Zepto, BigBasket), "US" (Kroger, Instacart) or "" for the
    device's time zone."""
    language: str = "English"
    """Language for replies and meal ideas (English, Hindi, Bengali, Tamil...). Pantry names stay as they are."""
    home_cuisine: str = ""
    """The food the household grew up with ("Bengali"), which can differ from where they live now:
    people move for work. Empty: not asked yet; "local" means the same as where they live."""


class Notifications(ApiModel):
    """Where order updates go besides email."""

    whatsapp: str = ""
    """The household's WhatsApp number in E.164 (+919876543210); empty means email only."""


class Household(ApiModel):
    inventory: list[PantryItem] = Field(default_factory=list)
    grocery: list[GroceryItem] = Field(default_factory=list)
    timers: list[KitchenTimer] = Field(default_factory=list)
    meals: list[Meal] = Field(default_factory=list)
    preferences: FoodPreferences = Field(default_factory=FoodPreferences)
    notifications: Notifications = Field(default_factory=Notifications)


_SEED: list[tuple[str, str, float, str, Category, int]] = [
    ("Baby spinach", "🥬", 1, "bag", "produce", 1),
    ("Cilantro", "🌿", 1, "bunch", "produce", 1),
    ("Chicken thighs", "🍗", 500, "g", "protein", 2),
    ("Bell peppers", "🫑", 3, "pcs", "produce", 2),
    ("Greek yogurt", "🥣", 1, "tub", "dairy", 3),
    ("Milk", "🥛", 1, "L", "dairy", 4),
    ("Corn tortillas", "🫓", 8, "pcs", "grains", 5),
    ("Limes", "🍋", 2, "pcs", "produce", 6),
    ("Eggs", "🥚", 6, "pcs", "protein", 10),
    ("Cheddar", "🧀", 200, "g", "dairy", 14),
    ("Garlic", "🧄", 1, "bulb", "produce", 20),
    ("Onions", "🧅", 3, "pcs", "produce", 25),
    ("Basmati rice", "🍚", 1, "kg", "grains", 180),
    ("Penne pasta", "🍝", 500, "g", "grains", 240),
    ("Crushed tomatoes", "🥫", 2, "can", "pantry", 300),
    ("Olive oil", "🫒", 1, "bottle", "condiments", 365),
]


# The judges' account: a Bengali family in Bengaluru, as in the demo film. Refreshed daily (see
# app.py) so the dates stay relative to today: spinach and coriander always expire tomorrow.
DEMO_PANTRY: list[tuple[str, str, float, str, Category, int]] = [
    ("Spinach", "🥬", 1, "bunch", "produce", 1),
    ("Coriander", "🌿", 1, "bunch", "produce", 1),
    ("Rohu fish", "🐟", 500, "g", "protein", 1),
    ("Paneer", "🧀", 200, "g", "dairy", 2),
    ("Milk", "🥛", 1, "L", "dairy", 2),
    ("Tomatoes", "🍅", 500, "g", "produce", 3),
    ("Curd", "🥣", 400, "g", "dairy", 3),
    ("Brinjal", "🍆", 2, "pcs", "produce", 4),
    ("Green chillies", "🌶️", 100, "g", "produce", 5),
    ("Eggs", "🥚", 6, "pcs", "protein", 8),
    ("Ginger", "🫚", 100, "g", "produce", 14),
    ("Garlic", "🧄", 1, "bulb", "produce", 20),
    ("Potatoes", "🥔", 1, "kg", "produce", 25),
    ("Onions", "🧅", 1, "kg", "produce", 25),
    ("Atta", "🌾", 1, "kg", "grains", 90),
    ("Basmati rice", "🍚", 1, "kg", "grains", 180),
    ("Red lentils", "🫘", 500, "g", "pantry", 180),
    ("Mustard oil", "🫒", 500, "ml", "condiments", 365),
]
DEMO_GROCERY = ["Mustard seeds", "Bananas", "Bread"]


def demo_inventory() -> list[PantryItem]:
    today = date.today()
    return [
        PantryItem(id=f"demo-{i}", name=name, emoji=emoji, quantity=qty, unit=unit, category=category,
                   expires_on=today + timedelta(days=days), source="seed")
        for i, (name, emoji, qty, unit, category, days) in enumerate(DEMO_PANTRY)
    ]


def seed_household() -> Household:
    """A believable starting pantry so a fresh demo has something to plan around."""
    today = date.today()
    return Household(
        inventory=[
            PantryItem(
                id=f"seed-{i}",
                name=name,
                emoji=emoji,
                quantity=qty,
                unit=unit,
                category=category,
                expires_on=today + timedelta(days=days),
                source="seed",
            )
            for i, (name, emoji, qty, unit, category, days) in enumerate(_SEED)
        ]
    )
