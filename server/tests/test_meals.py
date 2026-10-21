from datetime import datetime

import anyio
from mcp import Client
from starlette.testclient import TestClient

from pantry_pilot.mcp_server import create_server
from pantry_pilot.mealtime import occasion_at, user_timezone
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore


def at(hour, minute=0):
    return occasion_at(datetime(2026, 10, 20, hour, minute))


def test_occasion_follows_local_time():
    assert [at(4, 59), at(5), at(10, 29), at(10, 30), at(12), at(15, 30), at(18, 30), at(22, 30), at(23, 59)] == [
        "late-night snack", "breakfast", "breakfast", "brunch", "lunch",
        "evening snacks", "dinner", "late-night snack", "late-night snack",
    ]


class IdeasNova(MockNova):
    """Returns canned dishes and records what Nova was asked."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.requests = []

    def suggest_meals(self, system, request):
        self.requests.append((system, request))
        return self.batches.pop(0)


def plan(store, nova, args=None, zone=None):
    async def run():
        token = user_timezone.set(zone)
        try:
            async with Client(create_server(store, nova, lambda: "test")) as client:
                result = await client.call_tool("plan_meals", args or {})
                assert not result.is_error, result.content
                return result.structured_content
        finally:
            user_timezone.reset(token)

    return anyio.run(run)


UPMA = {
    "name": "Vegetable Upma", "emoji": "🥣", "minutes": 20, "servings": 2,
    "uses": ["baby spinach", "Onion", "Unicorn dust"], "missing": ["Semolina", "onion"],
    "steps": ["Roast the semolina.", "Cook the vegetables.", "Add water and stir."],
    "timer_label": "upma", "timer_minutes": 8,
}
POHA = {"name": "Poha", "emoji": "🍚", "minutes": 15, "servings": 2, "uses": [], "missing": [], "steps": ["Rinse.", "Cook."]}


def test_nova_meals_are_checked_against_the_pantry():
    store = MemoryStore()
    nova = IdeasNova([[UPMA, POHA]])
    result = plan(store, nova, {"meal": "breakfast", "request": "something light"})

    top = result["meals"][0]
    assert result["occasion"] == "breakfast" and "Amazon Nova" in result["summary"]
    assert top["name"] == "Vegetable Upma"
    assert "Baby spinach" in top["uses"] and "Unicorn dust" not in top["uses"]
    assert top["missing"] == ["Semolina"]  # onion is in the pantry
    assert "Baby spinach" in top["rescues"] and top["timer"] == {"label": "upma", "minutes": 8}
    assert "breakfast" in nova.requests[0][0] and "something light" in nova.requests[0][1]
    assert [m.occasion for m in store.load("test").meals] == ["breakfast", "breakfast"]


def test_replanning_asks_for_different_dishes():
    store = MemoryStore()
    nova = IdeasNova([[UPMA], [POHA]])
    plan(store, nova, {"meal": "breakfast"})
    second = plan(store, nova, {"meal": "breakfast"})
    assert "Vegetable Upma" in nova.requests[1][1]
    assert second["meals"][0]["name"] == "Poha"


def test_falls_back_to_recipes_without_nova():
    store = MemoryStore()
    result = plan(store, MockNova(), {"meal": "dinner"})
    assert "built-in recipes" in result["summary"] and result["occasion"] == "dinner"


def test_meal_defaults_to_the_device_time_zone():
    store = MemoryStore()
    # One of these two zones is always at a different meal than the other (12 hours apart).
    kolkata = plan(store, MockNova(), zone="Asia/Kolkata")["occasion"]
    chicago = plan(store, MockNova(), zone="America/Chicago")["occasion"]
    assert kolkata in {"breakfast", "brunch", "lunch", "evening snacks", "dinner", "late-night snack"}
    assert kolkata != chicago


def test_timezone_header_sets_the_request_zone():
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from pantry_pilot.mealtime import TimezoneMiddleware

    async def echo(_):
        return JSONResponse({"zone": user_timezone.get()})

    app = Starlette(routes=[Route("/", echo)])
    app.add_middleware(TimezoneMiddleware)
    with TestClient(app) as client:
        assert client.get("/", headers={"X-Timezone": "Asia/Kolkata"}).json() == {"zone": "Asia/Kolkata"}
        assert client.get("/", headers={"X-Timezone": "Not/AZone"}).json() == {"zone": None}
        assert client.get("/").json() == {"zone": None}


def call_tool(store, name, args):
    async def run():
        async with Client(create_server(store, MockNova(), lambda: "test")) as client:
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content
            return result.structured_content

    return anyio.run(run)


def test_preferences_are_saved_and_sent_to_nova():
    store = MemoryStore()
    saved = call_tool(store, "set_food_preferences", {"diet": "vegetarian", "cuisines": ["South Indian"], "avoid": ["mushroom"]})
    assert saved["summary"] == "Saved: vegetarian · South Indian · no mushroom"
    call_tool(store, "set_food_preferences", {"cuisines": []})  # other fields stay
    prefs = store.load("test").preferences
    assert (prefs.diet, prefs.cuisines, prefs.avoid) == ("vegetarian", [], ["mushroom"])

    nova = IdeasNova([[{**POHA, "name": "Mushroom Poha"}, POHA]])
    result = plan(store, nova, {"meal": "breakfast"}, zone="Asia/Kolkata")
    system, request = nova.requests[0]
    assert "in India" in system
    assert "no meat, poultry, fish or eggs" in request and "Never use: mushroom" in request
    assert [m["name"] for m in result["meals"]] == ["Poha"]  # the mushroom dish is dropped


def test_recipe_fallback_respects_diet():
    store = MemoryStore()
    call_tool(store, "set_food_preferences", {"diet": "vegetarian"})
    names = [m["name"] for m in plan(store, MockNova(), {"meal": "dinner"})["meals"]]
    assert names and not any("Chicken" in n or "Frittata" in n or "Fried Rice" in n for n in names)


def test_replanning_drops_repeated_dishes_even_if_nova_repeats_them():
    store = MemoryStore()
    nova = IdeasNova([[UPMA], [UPMA, POHA]])
    plan(store, nova, {"meal": "breakfast"})
    second = plan(store, nova, {"meal": "breakfast"})
    assert [m["name"] for m in second["meals"]] == ["Poha"]


def test_cancel_timer_stops_by_label_or_all():
    store = MemoryStore()
    call_tool(store, "set_timer", {"label": "rice", "minutes": 15})
    call_tool(store, "set_timer", {"label": "dal", "minutes": 20})
    one = call_tool(store, "cancel_timer", {"label": "rice"})
    assert one["stopped"] == ["rice"] and one["still_running"] == ["dal"]
    rest = call_tool(store, "cancel_timer", {})
    assert rest["stopped"] == ["dal"] and store.load("test").timers == []
    assert call_tool(store, "cancel_timer", {})["summary"] == "No timers were running"


def test_saved_city_steers_meal_ideas():
    store = MemoryStore()
    saved = call_tool(store, "set_food_preferences", {"location": "Bengaluru, Karnataka, India"})
    assert saved["summary"] == "Saved: Bengaluru, Karnataka, India · any diet"
    nova = IdeasNova([[POHA]])
    plan(store, nova, {"meal": "breakfast"}, zone="Asia/Kolkata")
    system = nova.requests[0][0]
    assert "home cook in Bengaluru, Karnataka, India" in system and "that city and state" in system


def test_home_cuisine_beats_the_city_for_people_who_moved():
    store = MemoryStore()
    saved = call_tool(store, "set_food_preferences", {"location": "Bengaluru, Karnataka, India", "home_cuisine": "Bengali"})
    assert saved["summary"] == "Saved: Bengaluru, Karnataka, India · Bengali home food · any diet"
    nova = IdeasNova([[POHA]])
    result = plan(store, nova, {"meal": "dinner"}, zone="Asia/Kolkata")
    system = nova.requests[0][0]
    assert "home cook in Bengaluru, Karnataka, India" in system and "grew up eating Bengali food" in system
    assert "ask_home_cuisine" not in result


def test_planner_asks_for_home_food_until_it_is_known():
    store = MemoryStore()
    assert plan(store, MockNova(), {"meal": "lunch"})["ask_home_cuisine"] is True
    call_tool(store, "set_food_preferences", {"home_cuisine": "local"})
    assert "ask_home_cuisine" not in plan(store, MockNova(), {"meal": "lunch"})


def test_reply_language_reaches_the_meal_prompt():
    store = MemoryStore()
    saved = call_tool(store, "set_food_preferences", {"language": "bengali"})
    assert saved["preferences"]["language"] == "Bengali" and "replies in Bengali" in saved["summary"]
    nova = IdeasNova([[POHA]])
    plan(store, nova, {"meal": "dinner"})
    assert "in Bengali, with the English dish name in brackets" in nova.requests[0][0]
