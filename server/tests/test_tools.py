import anyio
import pytest
from mcp import Client

from pantry_pilot.mcp_server import create_server
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore


@pytest.fixture
def store():
    return MemoryStore()


def call(store, name, args=None):
    async def run():
        async with Client(create_server(store, MockNova(), lambda: "test")) as client:
            result = await client.call_tool(name, args or {})
            assert not result.is_error, result.content
            return result.structured_content

    return anyio.run(run)


def stock(store):
    return {i.name: (i.quantity, i.unit) for i in store.load("test").inventory}


def test_lists_all_tools_with_read_only_hints(store):
    async def run():
        async with Client(create_server(store, MockNova(), lambda: "test")) as client:
            return {t.name: bool(t.annotations and t.annotations.read_only_hint) for t in (await client.list_tools()).tools}

    tools = anyio.run(run)
    assert tools == {
        "get_inventory": True,
        "update_inventory": False,
        "scan_pantry": True,
        "plan_meals": False,
        "set_food_preferences": False,
        "build_grocery_list": False,
        "get_grocery_list": True,
        "remove_from_grocery_list": False,
        "set_timer": False,
        "cancel_timer": False,
    }


def test_add_merges_with_existing_stock_and_converts_units(store):
    call(store, "update_inventory", {"action": "add", "items": [
        {"name": "Eggs", "quantity": 12, "unit": "pcs"},
        {"name": "basmati rice", "quantity": 500, "unit": "g"},
        {"name": "Paneer", "quantity": 200, "unit": "g", "category": "dairy", "expires_in_days": 5},
        {"name": "Tomatoes", "quantity": 1, "unit": "kg", "category": "produce"},
    ]})
    items = stock(store)
    assert items["Eggs"] == (18, "pcs")
    assert items["Basmati rice"] == (1.5, "kg")
    assert items["Paneer"] == (200, "g")
    # A new product must not merge into a similarly named one.
    assert items["Tomatoes"] == (1, "kg")
    assert items["Crushed tomatoes"] == (2, "can")


def test_use_subtracts_and_removes_when_empty(store):
    call(store, "update_inventory", {"action": "use", "items": [{"name": "eggs", "quantity": 2, "unit": "pcs"}]})
    assert stock(store)["Eggs"] == (4, "pcs")
    result = call(store, "update_inventory", {"action": "use", "items": [{"name": "Limes", "quantity": 5, "unit": "pcs"}]})
    assert "Limes" not in stock(store)
    assert result["changed"] == ["Limes"]


def test_remove_reports_unknown_items(store):
    result = call(store, "update_inventory", {"action": "remove", "items": [{"name": "onions"}, {"name": "caviar"}]})
    assert "Onions" not in stock(store)
    assert result["not_found"] == ["caviar"]


def test_plan_meals_prefers_expiring_food_and_saves_plan(store):
    result = call(store, "plan_meals")
    assert result["meals"][0]["name"] == "Chicken & Spinach Rice Bowl"
    assert "Baby spinach" in result["meals"][0]["rescues"]
    assert [m.id for m in store.load("test").meals][0] == "rice-bowl"


def test_grocery_list_from_top_meal_skips_duplicates(store):
    call(store, "plan_meals")
    call(store, "build_grocery_list", {"items": ["soy sauce"]})
    result = call(store, "build_grocery_list", {"items": ["Soy sauce", "ghee"]})
    assert result["added"] == ["Ghee"]
    assert call(store, "get_grocery_list")["items"] == ["Soy sauce", "Ghee"]


def test_scan_returns_detections_without_saving(store):
    before = stock(store)
    result = call(store, "scan_pantry", {"images": ["data:image/jpeg;base64,/9j/4AAQ"]})
    assert len(result["items"]) == 6
    assert result["unsure"] == 2
    assert stock(store) == before


def test_detections_use_kg_and_litres_for_large_amounts():
    from pantry_pilot.nova import _clean_detection

    base = {"category": "produce", "expires_in_days": 5, "confidence": 0.9}
    assert (d := _clean_detection(base | {"name": "Tomatoes", "quantity": 1020, "unit": "g"})) and (d.quantity, d.unit) == (1.02, "kg")
    assert (d := _clean_detection(base | {"name": "Milk", "quantity": 1500, "unit": "ml"})) and (d.quantity, d.unit) == (1.5, "L")
    assert (d := _clean_detection(base | {"name": "Paneer", "quantity": 200, "unit": "g"})) and (d.quantity, d.unit) == (200, "g")
    assert _clean_detection(base | {"name": "Ghost", "quantity": 0, "unit": "g"}) is None


def test_set_timer(store):
    result = call(store, "set_timer", {"label": "rice", "minutes": 15})
    assert result["seconds"] == 900
    assert store.load("test").timers[0].label == "rice"


def test_grocery_from_a_meal_adds_only_its_missing_items_and_items_can_be_removed(store):
    call(store, "plan_meals", {"meal": "dinner"})
    added = call(store, "build_grocery_list", {})["added"]
    missing = store.load("test").meals[0].missing
    assert added == missing  # no expired pantry items slipped in
    call(store, "build_grocery_list", {"items": ["milk", "eggs"]})
    result = call(store, "remove_from_grocery_list", {"items": ["Milk"]})
    assert result["removed"] == ["Milk"] and "Milk" not in call(store, "get_grocery_list")["items"]



def test_adds_without_expiry_use_novas_shelf_life(store):
    class ShelfNova(MockNova):
        def extract(self, system, text, name, schema):
            assert name == "report_shelf_life"
            return {"items": [{"name": "Chicken", "category": "protein", "expires_in_days": 2},
                              {"name": "Chickpeas", "category": "pantry", "expires_in_days": 300}]}

    async def run():
        async with Client(create_server(store, ShelfNova(), lambda: "test")) as client:
            await client.call_tool("update_inventory", {"action": "add", "items": [
                {"name": "Chicken", "quantity": 1, "unit": "kg"},
                {"name": "Chickpeas", "quantity": 1, "unit": "kg"},
                {"name": "Saffron", "quantity": 1, "unit": "g"},
            ]})

    anyio.run(run)
    items = {i.name: (i.category, i.days_left) for i in store.load("test").inventory}
    assert items["Chicken"] == ("protein", 2)
    assert items["Chickpeas"] == ("pantry", 300)
    assert items["Saffron"] == ("pantry", 180)  # Nova didn't mention it: category default
