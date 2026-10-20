"""Connectors to grocery apps' official MCP servers (Swiggy Instamart, Zepto).

Pantry Pilot is an MCP *client* here: it signs in to each app on the user's behalf (OAuth 2.1
with PKCE and dynamic client registration), reads recent orders, and adds what was delivered to
the pantry.

Safety: orders placed through these servers are real and can't be cancelled, so only the tools in
each provider's allowlist can ever be called. Ordering and payment tools are never on it.

Sign-in: both apps only accept their own allowlisted redirect URLs, including
http://localhost/callback. After signing in, the browser lands on that address (which shows an
error page because nothing runs there); the user copies the address back into Pantry Pilot, which
then finishes the sign-in. Each order is imported once.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

import anyio
from mcp import Client

from .models import DetectedItem
from .nova import Nova, OrderRead
from .store import Store

REDIRECT_URI = "http://localhost/callback"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    mcp_url: str
    scope: str | None
    read_tools: frozenset[str]
    """The only tools Pantry Pilot may call. Never ordering, payment or account changes."""
    direct_return: bool = False
    """The provider accepts our own https callback, so sign-in returns straight to the app.
    Otherwise it only allows http://localhost/callback and the user pastes that address back."""
    has_orders: bool = True
    """Shares order history to import. BigBasket doesn't: search and cart only."""


PROVIDERS: dict[str, Provider] = {
    "zepto": Provider(
        id="zepto",
        name="Zepto",
        mcp_url="https://mcp.zepto.co.in/mcp",
        # Cart and checkout too, so Alexa can order (only after the user confirms; see ordering.py).
        scope=" ".join([
            "tools:read", "tools:write",
            "dev.ucp.shopping.catalog.search:read", "dev.ucp.shopping.catalog.lookup:read",
            "dev.ucp.common.location.search:read", "dev.ucp.common.location.lookup:read",
            "dev.ucp.shopping.cart:manage", "dev.ucp.shopping.checkout:manage", "dev.ucp.shopping.order:manage",
        ]),
        read_tools=frozenset({"list_order_history", "get_order_detail", "get_past_order_items"}),
        # Zepto rejects any redirect not on its allowlist (invalid_redirect_uri) until it approves ours.
    ),
    "swiggy": Provider(
        id="swiggy",
        name="Swiggy Instamart",
        mcp_url="https://mcp.swiggy.com/im",
        scope="mcp:tools",
        read_tools=frozenset({"get_orders"}),
        # Swiggy's allowlist (GET mcp.swiggy.com/auth/check-redirect-uri) accepts http://localhost/callback
        # but not our domain until Swiggy approves it; set direct_return=True once it does.
    ),
    "bigbasket": Provider(
        id="bigbasket",
        name="BigBasket",
        mcp_url="https://mcp.bigbasket.com/mcp",
        scope="read write cart products",  # search and cart; BigBasket's MCP can't place orders
        read_tools=frozenset(),
        has_orders=False,
    ),
}

MAX_ORDERS = 5
PENDING_SECONDS = 900
"""How long a started sign-in waits for the pasted callback address."""


class ConnectorError(Exception):
    """A problem the user can act on; the message is shown as-is."""


# ---------- storage keys ----------

_FIRST_SCOPES = {"zepto": "tools:read", "swiggy": "mcp:tools"}
"""Scopes the first clients were registered with; a client registered for other scopes gets its own key."""


def _client_key(provider: Provider, redirect_uri: str) -> str:
    suffix = "" if redirect_uri == REDIRECT_URI else "#" + hashlib.sha256(redirect_uri.encode()).hexdigest()[:12]
    if provider.scope and provider.scope != _FIRST_SCOPES.get(provider.id):
        suffix += "#scope-" + hashlib.sha256(provider.scope.encode()).hexdigest()[:8]
    return f"oauth-client#{provider.id}{suffix}"


def _state_key(state: str) -> str:
    """Which user started a sign-in, for the provider's redirect back (it carries no login)."""
    return f"oauth-state#{state}"


def redirect_uri_for(provider: Provider, app_url: str | None) -> str:
    if provider.direct_return and app_url and app_url.startswith("https://"):
        return f"{app_url}/api/connectors/{provider.id}/callback"
    return REDIRECT_URI


def _pending_key(user: str, provider: Provider) -> str:
    return f"{user}#oauth-pending#{provider.id}"


def _tokens_key(user: str, provider: Provider) -> str:
    return f"{user}#connector#{provider.id}"


def _synced_key(user: str, provider: Provider) -> str:
    return f"{user}#synced-orders#{provider.id}"


# ---------- HTTP helpers (blocking; call through anyio.to_thread) ----------

def _get_json(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=10) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ValueError):
        return None


def _post(url: str, data: dict[str, Any], *, as_json: bool = False) -> tuple[int, dict[str, Any]]:
    body = json.dumps(data).encode() if as_json else urllib.parse.urlencode(data).encode()
    headers = {"Content-Type": "application/json" if as_json else "application/x-www-form-urlencoded", "Accept": "application/json"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}


# ---------- OAuth ----------

def discover(provider: Provider) -> dict[str, Any]:
    """Find the provider's authorization server metadata (RFC 9728, then RFC 8414)."""
    mcp = urllib.parse.urlparse(provider.mcp_url)
    origin = f"{mcp.scheme}://{mcp.netloc}"
    resource = _get_json(f"{origin}/.well-known/oauth-protected-resource{mcp.path}") or _get_json(
        f"{origin}/.well-known/oauth-protected-resource"
    )
    if not resource or not resource.get("authorization_servers"):
        raise ConnectorError(f"Couldn't reach {provider.name}'s sign-in service. Try again later.")
    issuer = resource["authorization_servers"][0].rstrip("/")
    parsed = urllib.parse.urlparse(issuer)
    issuer_origin = f"{parsed.scheme}://{parsed.netloc}"
    for candidate in (
        f"{issuer_origin}/.well-known/oauth-authorization-server{parsed.path}",
        f"{issuer}/.well-known/oauth-authorization-server",
        f"{issuer_origin}/.well-known/oauth-authorization-server",
    ):
        meta = _get_json(candidate)
        if meta and meta.get("authorization_endpoint") and meta.get("token_endpoint"):
            return meta
    raise ConnectorError(f"Couldn't reach {provider.name}'s sign-in service. Try again later.")


def _registered_client(store: Store, provider: Provider, redirect_uri: str = REDIRECT_URI) -> dict[str, Any]:
    """One dynamically registered OAuth client per provider and redirect, shared by all users."""
    if client := store.load_record(_client_key(provider, redirect_uri)):
        return client
    meta = discover(provider)
    if not meta.get("registration_endpoint"):
        raise ConnectorError(f"{provider.name} doesn't allow new apps to connect yet.")
    status, reg = _post(
        meta["registration_endpoint"],
        {
            "client_name": "Pantry Pilot",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            **({"scope": provider.scope} if provider.scope else {}),
        },
        as_json=True,
    )
    if status >= 400 or "client_id" not in reg:
        raise ConnectorError(f"{provider.name} refused to register Pantry Pilot ({reg.get('error', status)}).")
    client = {
        "client_id": reg["client_id"],
        "client_secret": reg.get("client_secret"),
        "authorization_endpoint": meta["authorization_endpoint"],
        "token_endpoint": meta["token_endpoint"],
    }
    store.save_record(_client_key(provider, redirect_uri), client)
    return client


def start_sign_in(store: Store, user: str, provider: Provider, app_url: str | None = None) -> str:
    """Returns the URL to open; remembers the PKCE verifier and state for finish_sign_in.
    `app_url` (our public https origin) lets providers that allow it return straight to the app."""
    redirect_uri = redirect_uri_for(provider, app_url)
    client = _registered_client(store, provider, redirect_uri)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    now = time.time()
    store.save_record(
        _pending_key(user, provider), {"verifier": verifier, "state": state, "created": now, "redirect_uri": redirect_uri}
    )
    store.save_record(_state_key(state), {"user": user, "provider": provider.id, "created": now})
    query = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": provider.mcp_url,
        **({"scope": provider.scope} if provider.scope else {}),
    }
    return f"{client['authorization_endpoint']}?{urllib.parse.urlencode(query)}"


def _save_tokens(
    store: Store, user: str, provider: Provider, token: dict[str, Any], previous: dict[str, Any] | None = None, redirect_uri: str | None = None
) -> None:
    store.save_record(
        _tokens_key(user, provider),
        {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token") or (previous or {}).get("refresh_token"),
            "expires_at": time.time() + float(token.get("expires_in") or 3600),
            "connected_at": (previous or {}).get("connected_at") or time.time(),
            "last_sync": (previous or {}).get("last_sync"),
            # Refreshing must use the client registered for this redirect, or the provider rejects it.
            "redirect_uri": redirect_uri or (previous or {}).get("redirect_uri") or REDIRECT_URI,
        },
    )


def finish_sign_in(store: Store, user: str, provider: Provider, redirect_url: str) -> None:
    """Completes sign-in from the http://localhost/callback?code=… address the user pasted back."""
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(redirect_url.strip()).query))
    pending = store.load_record(_pending_key(user, provider))
    if params.get("error"):
        raise ConnectorError(f"{provider.name} sign-in was cancelled or failed ({params['error']}).")
    if not pending or not params.get("code"):
        raise ConnectorError("That doesn't look like the address from the sign-in page. Start again and copy the full address.")
    if params.get("state") != pending["state"] or time.time() - pending["created"] > PENDING_SECONDS:
        raise ConnectorError("That sign-in link has expired. Start again.")
    redirect_uri = pending.get("redirect_uri", REDIRECT_URI)
    client = _registered_client(store, provider, redirect_uri)
    store.delete_record(_state_key(pending["state"]))
    status, token = _post(
        client["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": redirect_uri,
            "client_id": client["client_id"],
            "code_verifier": pending["verifier"],
            "resource": provider.mcp_url,
            **({"client_secret": client["client_secret"]} if client.get("client_secret") else {}),
        },
    )
    store.delete_record(_pending_key(user, provider))
    if status >= 400 or "access_token" not in token:
        raise ConnectorError(f"{provider.name} didn't accept the sign-in ({token.get('error', status)}). Start again.")
    _save_tokens(store, user, provider, token, redirect_uri=redirect_uri)


def finish_from_redirect(store: Store, provider: Provider, redirect_url: str) -> str:
    """The provider sent the browser back to our own callback. The request carries no login, so the
    one-time `state` identifies who started the sign-in. Returns that user."""
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(redirect_url).query))
    started = store.load_record(_state_key(params.get("state", ""))) if params.get("state") else None
    if not started or started["provider"] != provider.id:
        raise ConnectorError("That sign-in link has expired. Start again from Pantry Pilot.")
    finish_sign_in(store, started["user"], provider, redirect_url)
    return started["user"]


def access_token(store: Store, user: str, provider: Provider) -> str:
    saved = store.load_record(_tokens_key(user, provider))
    if not saved:
        raise ConnectorError(f"Connect {provider.name} first.")
    if saved["expires_at"] - time.time() > 60:
        return saved["access_token"]
    client = _registered_client(store, provider, saved.get("redirect_uri", REDIRECT_URI))
    if saved.get("refresh_token"):
        status, token = _post(
            client["token_endpoint"],
            {
                "grant_type": "refresh_token",
                "refresh_token": saved["refresh_token"],
                "client_id": client["client_id"],
                "resource": provider.mcp_url,
                **({"client_secret": client["client_secret"]} if client.get("client_secret") else {}),
            },
        )
        if status < 400 and "access_token" in token:
            _save_tokens(store, user, provider, token, previous=saved)
            return token["access_token"]
    store.delete_record(_tokens_key(user, provider))
    raise ConnectorError(f"Your {provider.name} sign-in has expired. Connect it again.")


def disconnect(store: Store, user: str, provider: Provider) -> None:
    store.delete_record(_tokens_key(user, provider))
    store.delete_record(_pending_key(user, provider))


def status(store: Store, user: str, app_url: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for p in PROVIDERS.values():
        saved = store.load_record(_tokens_key(user, p))
        synced = store.load_record(_synced_key(user, p)) or {}
        pending = store.load_record(_pending_key(user, p))
        direct = redirect_uri_for(p, app_url) != REDIRECT_URI
        rows.append({
            "id": p.id,
            "name": p.name,
            "connected": bool(saved),
            # Sign-in comes back to the app by itself; otherwise the user pastes the localhost address.
            "directReturn": direct,
            # A sign-in was started and is waiting for the pasted callback address.
            "pending": not direct and bool(pending) and time.time() - pending["created"] < PENDING_SECONDS,
            "lastSync": saved.get("last_sync") if saved else None,
            "importedOrders": len(synced.get("order_ids", [])),
            "hasOrders": p.has_orders,
        })
    return rows


# ---------- reading orders ----------

ClientFactory = Callable[[Provider, str], AbstractAsyncContextManager[Client]]


@contextlib.asynccontextmanager
async def remote_client(provider: Provider, token: str) -> AsyncIterator[Client]:
    """MCP client for the provider's server, authenticated as the user."""
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http:
        async with Client(streamable_http_client(provider.mcp_url, http_client=http)) as client:
            yield client


async def _call(client: Client, provider: Provider, tool: str, args: dict[str, Any]) -> str:
    if tool not in provider.read_tools:
        raise PermissionError(f"Pantry Pilot never calls {provider.name}'s {tool} tool")
    result = await client.call_tool(tool, args)
    text = "\n".join(t for b in result.content if (t := getattr(b, "text", "")))
    if result.is_error:
        raise ConnectorError(f"{provider.name} returned an error: {text[:200] or tool}")
    return text or json.dumps(result.structured_content)


async def tool_catalog(
    store: Store, user: str, provider: Provider, client_factory: ClientFactory = remote_client
) -> list[dict[str, Any]]:
    """The provider's tools (name, description, argument names): no user data. Logged and kept, so
    ordering can be built on the real tool names."""
    token = await anyio.to_thread.run_sync(access_token, store, user, provider)
    try:
        async with client_factory(provider, token) as client:
            listed = await client.list_tools()
    except BaseExceptionGroup as group:
        raise _first_error(group) from None
    catalog = [
        {
            "name": t.name,
            "description": (t.description or "")[:300],
            "args": sorted((t.input_schema or {}).get("properties", {})),
            "required": (t.input_schema or {}).get("required", []),
        }
        for t in listed.tools
    ]
    store.save_record(f"tool-catalog#{provider.id}", {"tools": catalog, "at": time.time()})
    log.info("%s MCP tools: %s", provider.name, json.dumps(catalog))
    # Full descriptions and argument formats of the shopping tools, one log line each.
    for t in listed.tools:
        if t.name in _SHOPPING_TOOLS:
            log.info("%s tool %s: %s", provider.name, t.name, json.dumps({"description": t.description, "input": t.input_schema}))
    return catalog


_SHOPPING_TOOLS = {
    "search_products", "search_multiple_products", "get_past_order_items", "list_saved_addresses",
    "select_saved_address", "update_cart", "view_cart", "get_payment_methods", "create_order",
    "create_online_payment_order", "check_payment_status", "update_drop_zone",
    "get_addresses", "get_cart", "clear_cart", "get_payment_options", "checkout", "confirm_order",
}


def _first_error(exc: BaseException) -> BaseException:
    """The MCP client's task group wraps errors in ExceptionGroups; surface the real one."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


async def fetch_orders(client: Client, provider: Provider, nova: Nova, skip: set[str]) -> list[OrderRead]:
    """Recent orders with items, read through the provider's read-only tools."""
    if provider.id == "swiggy":
        raw = await _call(client, provider, "get_orders", {"count": MAX_ORDERS})
        return await anyio.to_thread.run_sync(nova.read_orders, [raw])

    # Zepto lists orders first, then needs one call per order for its items.
    history = await _call(client, provider, "list_order_history", {"limit": MAX_ORDERS, "pageNumber": 1})
    listed = await anyio.to_thread.run_sync(nova.read_orders, [history])
    wanted = [o for o in listed if o.status == "delivered" and o.order_id not in skip][:MAX_ORDERS]
    details = [await _call(client, provider, "get_order_detail", {"orderId": o.order_id}) for o in wanted]
    if not details:
        return listed
    read = {o.order_id: o for o in await anyio.to_thread.run_sync(nova.read_orders, details)}
    return [read.get(o.order_id, o) for o in listed]


@dataclass
class SyncResult:
    new_orders: list[str]
    items: list[DetectedItem]


async def sync_orders(
    store: Store, user: str, provider: Provider, nova: Nova, client_factory: ClientFactory = remote_client
) -> SyncResult:
    """New delivered orders since the last sync, merged into one item list (not yet saved)."""
    if not provider.has_orders:
        raise ConnectorError(f"{provider.name} doesn't share order history.")
    token = await anyio.to_thread.run_sync(access_token, store, user, provider)
    synced = store.load_record(_synced_key(user, provider)) or {"order_ids": []}
    seen = set(synced["order_ids"])
    try:
        async with client_factory(provider, token) as client:
            orders = await fetch_orders(client, provider, nova, seen)
    except BaseExceptionGroup as group:
        raise _first_error(group) from None

    fresh = [o for o in orders if o.status == "delivered" and o.order_id not in seen]
    synced["order_ids"] = sorted(seen | {o.order_id for o in fresh})
    store.save_record(_synced_key(user, provider), synced)
    saved = store.load_record(_tokens_key(user, provider))
    if saved:
        store.save_record(_tokens_key(user, provider), saved | {"last_sync": time.time()})
    return SyncResult(new_orders=[o.order_id for o in fresh], items=[i for o in fresh for i in o.items])
