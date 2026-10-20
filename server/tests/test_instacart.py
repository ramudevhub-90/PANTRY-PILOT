import anyio
from mcp import Client
from starlette.testclient import TestClient

from pantry_pilot import instacart as ic
from pantry_pilot.app import create_app
from pantry_pilot.models import GroceryItem
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore


def fake_api(monkeypatch, status=200):
    sent = []

    def post(url, body, key):
        sent.append((url, body, key))
        return status, {"products_link_url": f"https://www.instacart.com/store/shopping_lists/{len(sent)}"} if status == 200 else {}

    monkeypatch.setattr(ic, "_post", post)
    return sent


def with_list(store, *names, checked=()):
    household = store.load("demo")
    household.grocery = [GroceryItem(name=n) for n in names] + [GroceryItem(name=n, checked=True) for n in checked]
    store.save("demo", household)


def test_shop_button_sends_open_items_and_reuses_the_link(monkeypatch):
    sent = fake_api(monkeypatch)
    store = MemoryStore()
    with_list(store, "Milk", "Eggs", checked=["Bread"])
    app = create_app(store, MockNova(), "demo", instacart=ic.Instacart("development", lambda: "dev-key"))
    with TestClient(app, base_url="https://trypantrypilot.com") as client:
        assert client.get("/api/instacart").json() == {"enabled": True}
        first = client.post("/api/instacart").json()
        assert first["items"] == ["Milk", "Eggs"] and first["url"].endswith("/1")

        url, body, key = sent[0]
        assert url == "https://connect.dev.instacart.tools/idp/v1/products/products_link" and key == "dev-key"
        assert [li["name"] for li in body["line_items"]] == ["Milk", "Eggs"]
        assert body["landing_page_configuration"]["partner_linkback_url"] == "https://trypantrypilot.com"

        assert client.post("/api/instacart").json()["url"] == first["url"]  # same list, same link
        assert len(sent) == 1

        with_list(store, "Milk", "Eggs", "Paneer")
        assert client.post("/api/instacart").json()["url"].endswith("/2")


def test_alexa_tool_and_friendly_errors(monkeypatch):
    fake_api(monkeypatch, status=500)
    store = MemoryStore()
    off = create_app(store, MockNova(), "demo", instacart=ic.Instacart("production", lambda: None))
    with TestClient(off) as client:
        assert client.get("/api/instacart").json() == {"enabled": False}
        with_list(store, "Milk")
        assert client.post("/api/instacart").json()["error"] == "Instacart isn't set up yet."

    app = create_app(MemoryStore(), MockNova(), "demo", instacart=ic.Instacart("production", lambda: "k"))
    with TestClient(app) as client:
        assert "empty" in client.post("/api/instacart").json()["error"]

    async def via_mcp():
        async with Client(app.state.mcp) as c:
            return (await c.call_tool("shop_on_instacart", {})).structured_content

    assert "empty" in anyio.run(via_mcp)["summary"]
