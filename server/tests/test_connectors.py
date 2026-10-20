import contextlib
import dataclasses
import json
import time
import urllib.parse

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from pantry_pilot import connectors
from pantry_pilot.app import create_app
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore

ZEPTO, SWIGGY = connectors.PROVIDERS["zepto"], connectors.PROVIDERS["swiggy"]


def item(name, quantity, unit, category="produce", days=5):
    return {"name": name, "quantity": quantity, "unit": unit, "category": category,
            "expires_in_days": days, "confidence": 0.95}


def fake_provider():
    """A stand-in for Zepto's and Swiggy's MCP servers. Data comes back in the shape MockNova reads."""
    server = MCPServer(name="fake-grocery")
    calls: list[str] = []

    @server.tool()
    def list_order_history(limit: int = 5, pageNumber: int = 1) -> str:
        calls.append("list_order_history")
        return json.dumps({"orders": [
            {"order_id": "Z1", "status": "delivered", "items": []},
            {"order_id": "Z2", "status": "cancelled", "items": []},
        ]})

    @server.tool()
    def get_order_detail(orderId: str) -> str:
        calls.append(f"get_order_detail:{orderId}")
        return json.dumps({"orders": [{"order_id": orderId, "status": "delivered", "items": [
            item("Ponni rice", 5, "kg", "grains", 180), item("Moong dal", 500, "g", "grains", 180)]}]})

    @server.tool()
    def get_orders(count: int = 5) -> str:
        calls.append("get_orders")
        return json.dumps({"orders": [{"order_id": "S1", "status": "delivered", "items": [item("Paneer", 400, "g", "dairy", 5)]}]})

    @server.tool()
    def create_order() -> str:
        calls.append("create_order")
        return "ORDER PLACED"

    @contextlib.asynccontextmanager
    async def factory(provider, token):
        assert token == "tok"
        async with Client(server) as client:
            yield client

    return factory, calls


def connected(store, user="alice", *providers):
    for p in providers or (ZEPTO, SWIGGY):
        store.save_record(connectors._tokens_key(user, p),
                          {"access_token": "tok", "refresh_token": "r", "expires_at": time.time() + 3600})


def test_zepto_sync_imports_delivered_orders_once():
    store, (factory, calls) = MemoryStore(), fake_provider()
    connected(store)

    first = anyio.run(connectors.sync_orders, store, "alice", ZEPTO, MockNova(), factory)
    assert first.new_orders == ["Z1"]  # Z2 was cancelled
    assert [(i.name, i.quantity, i.unit) for i in first.items] == [("Ponni rice", 5, "kg"), ("Moong dal", 500, "g")]
    assert calls == ["list_order_history", "get_order_detail:Z1"]

    second = anyio.run(connectors.sync_orders, store, "alice", ZEPTO, MockNova(), factory)
    assert second.new_orders == [] and second.items == []
    assert "get_order_detail:Z1" not in calls[2:]  # already imported, not fetched again


def test_swiggy_sync_reads_order_history():
    store, (factory, calls) = MemoryStore(), fake_provider()
    connected(store)
    result = anyio.run(connectors.sync_orders, store, "alice", SWIGGY, MockNova(), factory)
    assert result.new_orders == ["S1"] and result.items[0].name == "Paneer"
    assert calls == ["get_orders"]


def test_ordering_tools_can_never_be_called():
    factory, calls = fake_provider()

    async def attempt():
        async with factory(ZEPTO, "tok") as client:
            try:
                await connectors._call(client, ZEPTO, "create_order", {})
            except PermissionError:
                return "blocked"
        return "called"

    assert anyio.run(attempt) == "blocked"
    assert "create_order" not in calls
    for provider in connectors.PROVIDERS.values():
        risky = [t for t in provider.read_tools if any(w in t for w in ("create", "checkout", "confirm", "pay", "cart"))]
        assert risky == [], f"{provider.name} allowlist must stay read-only"


def test_sync_requires_a_connection():
    with pytest.raises(connectors.ConnectorError, match="Connect Zepto first"):
        anyio.run(connectors.sync_orders, MemoryStore(), "alice", ZEPTO, MockNova(), fake_provider()[0])


@pytest.fixture
def fake_oauth(monkeypatch):
    posts = []
    monkeypatch.setattr(connectors, "_get_json", lambda url: {
        "authorization_servers": ["https://auth.example"],
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "https://auth.example/register",
    })

    def post(url, data, as_json=False):
        posts.append((url, data))
        if url.endswith("/register"):
            return 201, {"client_id": "pp-client"}
        return 200, {"access_token": "new-tok", "refresh_token": "r1", "expires_in": 3600}

    monkeypatch.setattr(connectors, "_post", post)
    return posts


def test_sign_in_uses_pkce_and_finishes_from_the_pasted_address(fake_oauth):
    store = MemoryStore()
    url = urllib.parse.urlparse(connectors.start_sign_in(store, "alice", ZEPTO))
    assert [r["pending"] for r in connectors.status(store, "alice")] == [True, False, False]
    query = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "auth.example"
    assert query["client_id"] == "pp-client" and query["redirect_uri"] == "http://localhost/callback"
    assert query["code_challenge_method"] == "S256"
    assert {"tools:read", "dev.ucp.shopping.cart:manage", "dev.ucp.shopping.checkout:manage"} <= set(query["scope"].split())
    assert query["resource"] == "https://mcp.zepto.co.in/mcp"

    with pytest.raises(connectors.ConnectorError):
        connectors.finish_sign_in(store, "alice", ZEPTO, "http://localhost/callback?code=c&state=wrong")

    pasted = f"http://localhost/callback?code=the-code&state={query['state']}"
    connectors.finish_sign_in(store, "alice", ZEPTO, pasted)
    token_request = fake_oauth[-1][1]
    assert token_request["code"] == "the-code" and len(token_request["code_verifier"]) > 40
    assert connectors.access_token(store, "alice", ZEPTO) == "new-tok"
    assert [r["connected"] for r in connectors.status(store, "alice")] == [True, False, False]
    assert [r["pending"] for r in connectors.status(store, "alice")] == [False, False, False]


def test_swiggy_returns_straight_to_the_app(fake_oauth, monkeypatch):
    """For when Swiggy approves our domain; until then it uses the localhost paste flow."""
    monkeypatch.setitem(connectors.PROVIDERS, "swiggy", dataclasses.replace(SWIGGY, direct_return=True))
    store = MemoryStore()
    app = create_app(store, MockNova(), "alice")
    with TestClient(app, base_url="https://trypantrypilot.com") as client:
        started = client.post("/api/connectors/swiggy/start").json()
        assert started["directReturn"] is True
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(started["authUrl"]).query))
        callback = "https://trypantrypilot.com/api/connectors/swiggy/callback"
        assert query["redirect_uri"] == callback
        assert fake_oauth[0][1]["redirect_uris"] == [callback]  # registered with our address

        # Zepto still uses the localhost address it allows.
        assert client.post("/api/connectors/zepto/start").json()["directReturn"] is False

        bad = client.get("/api/connectors/swiggy/callback?code=c&state=forged", follow_redirects=False)
        assert bad.status_code == 303 and "connectError=" in bad.headers["location"]

        done = client.get(f"/api/connectors/swiggy/callback?code=the-code&state={query['state']}", follow_redirects=False)
        assert done.headers["location"] == "/?connected=swiggy"
        assert fake_oauth[-1][1]["redirect_uri"] == callback and fake_oauth[-1][1]["code"] == "the-code"
        assert connectors.access_token(store, "alice", SWIGGY) == "new-tok"

        # The state is single use.
        again = client.get(f"/api/connectors/swiggy/callback?code=x&state={query['state']}", follow_redirects=False)
        assert "connectError=" in again.headers["location"]


def test_refresh_uses_the_client_registered_for_our_address(fake_oauth):
    store = MemoryStore()
    swiggy = dataclasses.replace(SWIGGY, direct_return=True)
    url = connectors.start_sign_in(store, "alice", swiggy, "https://trypantrypilot.com")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["state"]
    connectors.finish_from_redirect(store, swiggy, f"https://trypantrypilot.com/api/connectors/swiggy/callback?code=c&state={state}")
    key = "alice#connector#swiggy"
    store.save_record(key, store.load_record(key) | {"expires_at": time.time() - 10})

    assert connectors.access_token(store, "alice", swiggy) == "new-tok"
    assert sum(url.endswith("/register") for url, _ in fake_oauth) == 1  # no second, localhost-only client
    assert fake_oauth[-1][1]["grant_type"] == "refresh_token"


def test_swiggy_uses_the_paste_flow_until_it_approves_our_domain(fake_oauth):
    url = connectors.start_sign_in(MemoryStore(), "alice", SWIGGY, "https://trypantrypilot.com")
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["redirect_uri"] == "http://localhost/callback"


def test_local_dev_keeps_the_paste_flow(fake_oauth):
    url = connectors.start_sign_in(MemoryStore(), "alice", SWIGGY, "http://localhost:8787")
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["redirect_uri"] == "http://localhost/callback"


def test_sync_endpoint_and_mcp_tool_add_orders_to_the_pantry():
    store, (factory, _) = MemoryStore(), fake_provider()
    connected(store, "demo")
    app = create_app(store, MockNova(), "demo", connector_client=factory)
    with TestClient(app) as client:
        response = client.post("/api/connectors/zepto/sync")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["sync"]["summary"] == "Imported 2 items from 1 new Zepto order"
        rice = next(i for i in body["state"]["inventory"] if i["name"] == "Ponni rice")
        assert rice["source"] == "order" and rice["quantity"] == 5

        status = {c["id"]: c for c in client.get("/api/connectors").json()["connectors"]}
        assert status["zepto"]["connected"] and status["zepto"]["importedOrders"] == 1
        assert client.post("/api/connectors/nope/sync").status_code == 404

    async def via_mcp():
        async with Client(app_mcp_server(store, factory)) as c:
            result = await c.call_tool("sync_grocery_orders", {"app": "swiggy"})
            return result.structured_content

    assert anyio.run(via_mcp)["summary"] == "Imported 1 item from 1 new Swiggy Instamart order"


def app_mcp_server(store, factory):
    """The MCP server inside create_app, reached through its streamable HTTP app's route."""
    app = create_app(store, MockNova(), "demo", connector_client=factory)
    return app.state.mcp


def test_tool_catalog_lists_names_without_user_data(fake_oauth):
    store = MemoryStore()
    url = urllib.parse.urlparse(connectors.start_sign_in(store, "alice", ZEPTO))
    state = dict(urllib.parse.parse_qsl(url.query))["state"]
    connectors.finish_sign_in(store, "alice", ZEPTO, f"http://localhost/callback?code=c&state={state}")

    class Tool:
        def __init__(self, name, props):
            self.name, self.description = name, f"{name} tool"
            self.input_schema = {"type": "object", "properties": {p: {} for p in props}, "required": props[:1]}

    class Listed:
        tools = [Tool("search_catalog", ["query"]), Tool("update_cart", ["items", "cart_id"])]

    class FakeClient:
        async def list_tools(self):
            return Listed()

    @contextlib.asynccontextmanager
    async def factory(provider, token):
        yield FakeClient()

    catalog = anyio.run(connectors.tool_catalog, store, "alice", ZEPTO, factory)
    assert [t["name"] for t in catalog] == ["search_catalog", "update_cart"]
    assert catalog[1]["args"] == ["cart_id", "items"] and catalog[1]["required"] == ["items"]
    assert store.load_record("tool-catalog#zepto")["tools"] == catalog


def test_bigbasket_registers_as_a_confidential_client_and_sends_its_secret(monkeypatch):
    posts = []
    monkeypatch.setattr(connectors, "discover", lambda provider: {
        "authorization_endpoint": "https://bb.example/authorize",
        "token_endpoint": "https://bb.example/token",
        "registration_endpoint": "https://bb.example/register",
    })

    def post(url, data, as_json=False):
        posts.append((url, data))
        if url.endswith("/register"):
            return 201, {"client_id": "bb-client", "client_secret": "bb-secret"}
        return 200, {"access_token": "tok", "expires_in": 3600}

    monkeypatch.setattr(connectors, "_post", post)
    store = MemoryStore()
    bigbasket = connectors.PROVIDERS["bigbasket"]
    url = urllib.parse.urlparse(connectors.start_sign_in(store, "alice", bigbasket))
    state = dict(urllib.parse.parse_qsl(url.query))["state"]
    connectors.finish_sign_in(store, "alice", bigbasket, f"http://localhost/callback?code=c&state={state}")
    assert posts[0][1]["token_endpoint_auth_method"] == "client_secret_post"
    assert posts[-1][1]["client_secret"] == "bb-secret"
