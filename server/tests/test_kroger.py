import json
import urllib.parse

import anyio
import pytest
from mcp import Client
from starlette.testclient import TestClient

from pantry_pilot import kroger as kr
from pantry_pilot.app import create_app
from pantry_pilot.models import GroceryItem
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore

MILK = {
    "upc": "0001111041700",
    "description": "Kroger® 2% Reduced Fat Milk",
    "brand": "Kroger",
    "items": [{"size": "1 gal"}],
    "images": [{"perspective": "front", "sizes": [{"size": "thumbnail", "url": "https://img/milk.jpg"}]}],
}


@pytest.fixture
def fake_kroger(monkeypatch):
    calls = []

    def request(method, url, body, headers):
        calls.append((method, url, body, headers))
        if url.endswith("/connect/oauth2/token"):
            form = dict(urllib.parse.parse_qsl(body.decode()))
            assert headers["Authorization"].startswith("Basic ")
            return 200, {"access_token": f"tok-{form['grant_type']}", "refresh_token": "r", "expires_in": 1800}
        if "/products?" in url:
            return 200, {"data": [MILK] if "milk" in url.lower() else []}
        if url.endswith("/cart/add"):
            assert headers["Authorization"] == "Bearer tok-authorization_code"
            return 204, {}
        return 404, {}

    monkeypatch.setattr(kr, "_request", request)
    return calls


def app_with_list(store, *names):
    household = store.load("alice")
    household.grocery = [GroceryItem(name=n) for n in names]
    store.save("alice", household)
    kroger = kr.Kroger("production", lambda: kr.Credentials("cid", "secret"))
    return create_app(store, MockNova(), "alice", kroger=kroger)


def test_connect_match_and_add_only_chosen_items(fake_kroger):
    store = MemoryStore()
    app = app_with_list(store, "Milk", "Saffron")
    with TestClient(app, base_url="https://trypantrypilot.com") as client:
        assert client.get("/api/kroger").json() == {"enabled": True, "connected": False}
        assert "Connect your Kroger account" in client.post("/api/kroger/cart", json={"items": [{"upc": "1"}]}).json()["error"]

        auth = urllib.parse.urlparse(
            client.post("/api/kroger/start", headers={"Origin": "https://trypantrypilot.com"}).json()["authUrl"]
        )
        query = dict(urllib.parse.parse_qsl(auth.query))
        assert auth.netloc == "api.kroger.com" and query["scope"] == "cart.basic:write"
        assert query["redirect_uri"] == "https://trypantrypilot.com/api/kroger/callback"
        assert query["code_challenge_method"] == "S256"

        forged = client.get("/api/kroger/callback?code=c&state=nope", follow_redirects=False)
        assert "connectError=" in forged.headers["location"]
        back = client.get(f"/api/kroger/callback?code=the-code&state={query['state']}", follow_redirects=False)
        assert back.headers["location"] == "https://trypantrypilot.com/?connected=kroger"
        token_form = dict(urllib.parse.parse_qsl(fake_kroger[-1][2].decode()))
        assert token_form["code"] == "the-code" and len(token_form["code_verifier"]) > 40
        assert client.get("/api/kroger").json()["connected"] is True

        matches = client.post("/api/kroger/match").json()["matches"]
        assert matches[0] == {"item": "Milk", "products": [{
            "upc": "0001111041700", "description": "Kroger® 2% Reduced Fat Milk", "brand": "Kroger",
            "size": "1 gal", "image": "https://img/milk.jpg"}]}
        assert matches[1] == {"item": "Saffron", "products": []}

        added = client.post("/api/kroger/cart", json={"items": [{"upc": "0001111041700", "quantity": 2}], "modality": "DELIVERY"})
        assert added.json() == {"added": 1, "cartUrl": "https://www.kroger.com/cart"}
        assert json.loads(fake_kroger[-1][2]) == {"items": [{"upc": "0001111041700", "quantity": 2, "modality": "DELIVERY"}]}

        assert client.post("/api/kroger/disconnect").json()["connected"] is False


def test_nothing_stored_but_tokens(fake_kroger):
    store = MemoryStore()
    app = app_with_list(store, "Milk")
    with TestClient(app, base_url="https://trypantrypilot.com") as client:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(client.post("/api/kroger/start").json()["authUrl"]).query))
        client.get(f"/api/kroger/callback?code=c&state={query['state']}", follow_redirects=False)
        client.post("/api/kroger/match")
        client.post("/api/kroger/cart", json={"items": [{"upc": "0001111041700"}]})
    kroger_records = [k for k in store._records if "kroger" in k]
    assert kroger_records == ["alice#kroger-tokens"]


def test_alexa_tools(fake_kroger):
    store = MemoryStore()
    app = app_with_list(store)
    with TestClient(app, base_url="https://trypantrypilot.com") as client:
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(client.post("/api/kroger/start").json()["authUrl"]).query))
        client.get(f"/api/kroger/callback?code=c&state={query['state']}", follow_redirects=False)

    async def via_mcp():
        async with Client(app.state.mcp) as c:
            found = (await c.call_tool("find_kroger_products", {"items": ["milk"]})).structured_content
            added = (await c.call_tool("add_to_kroger_cart", {"upcs": [found["matches"][0]["products"][0]["upc"]]})).structured_content
            return found, added

    found, added = anyio.run(via_mcp)
    assert found["summary"] == "Found 1 of 1 items at Kroger."
    assert added == {"added": 1, "summary": "Added 1 item to your Kroger cart. Check out on kroger.com."}
