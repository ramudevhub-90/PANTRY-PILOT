"""Price comparison and spoken-confirmation ordering on Zepto and Swiggy Instamart (their MCP servers).

Orders are real money and can't be cancelled from here, so the rules live in code, not in a prompt:
- `prepare` fills the app's cart and reads back the items, total, address and payment method; it
  never orders.
- `place` only orders when (1) a read-back exists from an EARLIER turn, under 5 minutes old, (2)
  the user's own words in THIS turn say "place the order" / "confirm order" (a bare "yes" or
  background noise is not enough), and (3) the app's live total still matches the read-back.
  Calls from outside the Pantry Pilot app (no turn) can never place orders.
- Cash on delivery by default; otherwise the app's own payment link is sent to the user. Pantry
  Pilot never sees card or UPI details.

The apps' replies are free-form text; Amazon Nova reads them into fields, and every product or
address ID it reports must appear verbatim in the reply, so a misread can't invent an ID.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any

import anyio

from . import connectors
from .nova import Nova
from .store import Store

DRAFT_SECONDS = 300
COMPARE_SECONDS = 15 * 60
CONFIRM = re.compile(r"\b(place|confirm)\b[\w\s,'’]{0,24}\border\b|\border\s+(it|now)\b", re.I)

# Tools Pantry Pilot may call for shopping, per app (on top of each connector's read-only tools).
SHOP_TOOLS: dict[str, frozenset[str]] = {
    "zepto": frozenset({
        "list_saved_addresses", "select_saved_address", "search_multiple_products", "update_cart",
        "view_cart", "get_payment_methods", "create_order", "create_online_payment_order",
    }),
    "swiggy": frozenset({"get_addresses", "search_products", "update_cart", "get_cart", "get_payment_options", "checkout"}),
}

current_turn: ContextVar[dict[str, str] | None] = ContextVar("current_turn", default=None)
"""{"id", "text"} of the user turn being answered in the Pantry Pilot app; None for outside MCP calls."""


class OrderError(Exception):
    """Something the user can act on; the message is spoken as-is."""


@dataclass
class Pick:
    item: str
    name: str
    pack: str
    price: float  # rupees, per unit
    ids: dict[str, str]
    quantity: int = 1


@dataclass
class Quote:
    app: str
    address_id: str
    address: str
    picks: list[Pick] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(p.price * p.quantity for p in self.picks), 2)


def rupees(amount: float) -> str:
    return f"₹{amount:,.0f}" if amount == int(amount) else f"₹{amount:,.2f}"


# ---------- talking to an app ----------

class AppSession:
    """One MCP session with the app, limited to its read and shopping tools."""

    def __init__(self, client: Any, provider: connectors.Provider) -> None:
        self.client, self.provider = client, provider
        self.allowed = provider.read_tools | SHOP_TOOLS.get(provider.id, frozenset())

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> str:
        if tool not in self.allowed:
            raise PermissionError(f"Pantry Pilot never calls {self.provider.name}'s {tool} tool")
        result = await self.client.call_tool(tool, args or {})
        text = "\n".join(t for b in result.content if (t := getattr(b, "text", "")))
        if result.is_error:
            raise OrderError(f"{self.provider.name} said: {text[:200] or tool}")
        return text or json.dumps(result.structured_content)


@contextlib.asynccontextmanager
async def app_session(
    store: Store, user: str, provider: connectors.Provider, factory: connectors.ClientFactory
) -> AsyncIterator[AppSession]:
    if provider.id not in SHOP_TOOLS:
        raise OrderError(f"Ordering on {provider.name} isn't available.")
    try:
        token = await anyio.to_thread.run_sync(connectors.access_token, store, user, provider)
    except connectors.ConnectorError as err:
        raise OrderError(str(err)) from None
    try:
        async with factory(provider, token) as client:
            yield AppSession(client, provider)
    except BaseExceptionGroup as group:
        raise connectors._first_error(group) from None


async def _read(nova: Nova, raw: str, what: str, name: str, schema: dict[str, Any]) -> dict[str, Any]:
    system = (
        f"You read a grocery app's reply and report {what}. Copy IDs, names and prices exactly as "
        "they appear; never invent an ID. Prices are in rupees (convert paisa if the reply uses paisa)."
    )
    return await anyio.to_thread.run_sync(nova.extract, system, raw, name, schema)


def _verified(value: Any, raw: str) -> str | None:
    """The ID as a string, only if it appears verbatim in the app's reply."""
    text = str(value or "").strip()
    return text if text and text in raw else None


_ADDRESS_SCHEMA = {
    "type": "object",
    "properties": {
        "address_id": {"type": "string", "description": "ID of the default (or first) saved address."},
        "label": {"type": "string", "description": "Short label, e.g. Home - HSR Layout."},
    },
    "required": ["address_id"],
}


def _picks_schema(id_fields: dict[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "found": {"type": "boolean"},
                        "name": {"type": "string", "description": "Product name with brand."},
                        "pack": {"type": "string", "description": "Pack size, e.g. 1 kg."},
                        "price": {"type": "number", "description": "Selling price in rupees."},
                        **{k: {"type": "string", "description": v} for k, v in id_fields.items()},
                    },
                    "required": ["query", "found"],
                },
            }
        },
        "required": ["items"],
    }


_ZEPTO_IDS = {"productVariantId": "productVariantId of the chosen product", "storeProductId": "storeProductId of the chosen product"}
_SWIGGY_IDS = {"spinId": "spinId of the chosen variant", "skuId": "skuId of the chosen variant, if any"}
_PICK_HINT = (
    "For each query pick the single best in-stock product: the closest match to what the query "
    "asks for, in a common household pack size, preferring the lower price when several fit. "
    "found=false when nothing suitable is in stock."
)


async def _address(s: AppSession, nova: Nova) -> tuple[str, str]:
    raw = await s.call("list_saved_addresses" if s.provider.id == "zepto" else "get_addresses")
    read = await _read(nova, raw, "the default delivery address", "report_address", _ADDRESS_SCHEMA)
    address_id = _verified(read.get("address_id"), raw)
    if not address_id:
        raise OrderError(f"Add a delivery address in the {s.provider.name} app first.")
    if s.provider.id == "zepto":
        await s.call("select_saved_address", {"addressId": address_id})
    return address_id, str(read.get("label") or "your saved address")[:80]


async def quote(s: AppSession, nova: Nova, items: list[str]) -> Quote:
    """Best product and price for each item at the user's default address."""
    address_id, label = await _address(s, nova)
    if s.provider.id == "zepto":
        raw = await s.call("search_multiple_products", {"queries": items})
        ids = _ZEPTO_IDS
    else:
        parts = []
        for item in items:
            parts.append(f"### {item}\n" + await s.call("search_products", {"query": item, "addressId": address_id}))
        raw, ids = "\n\n".join(parts), _SWIGGY_IDS
    read = await _read(nova, f"Queries: {json.dumps(items)}\n{_PICK_HINT}\n\n{raw}", "the best product per query", "report_picks", _picks_schema(ids))
    result = Quote(app=s.provider.id, address_id=address_id, address=label)
    by_query = {str(r.get("query", "")).lower(): r for r in read.get("items", [])}
    for item in items:
        r = by_query.get(item.lower()) or {}
        verified = {k: v for k in ids if (v := _verified(r.get(k), raw))}
        needed = ["productVariantId", "storeProductId"] if s.provider.id == "zepto" else ["spinId"]
        price = r.get("price")
        if not r.get("found") or any(k not in verified for k in needed) or not isinstance(price, int | float) or price <= 0:
            result.missing.append(item)
            continue
        result.picks.append(Pick(item, str(r.get("name") or item)[:80], str(r.get("pack") or "")[:30], float(price), verified))
    return result


_BILL_SCHEMA = {
    "type": "object",
    "properties": {
        "total": {"type": "number", "description": "Amount to pay in rupees, including fees."},
        "fees": {"type": "number", "description": "Delivery, handling and other fees in rupees."},
        "cod_available": {"type": "boolean", "description": "Cash on delivery offered for this cart."},
    },
    "required": ["total"],
}


async def _fill_and_bill(s: AppSession, nova: Nova, q: Quote, user: str) -> dict[str, Any]:
    """Put exactly the quoted picks in the app's cart and read the bill (no order is placed)."""
    if s.provider.id == "zepto":
        cart = [
            {"productVariantId": p.ids["productVariantId"], "storeProductId": p.ids["storeProductId"],
             "quantity": p.quantity, "name": p.name}
            for p in q.picks
        ]
        await s.call("update_cart", {"deviceId": f"pantry-pilot-{user[:24]}", "cartItems": cart, "replaceCart": True})
        payment = await s.call("get_payment_methods")
        preview = await s.call("create_order", {"confirmOrder": False, "userAddressId": q.address_id})
        raw = f"{preview}\n\nPayment methods:\n{payment}"
    else:
        items = [{"spinId": p.ids["spinId"], "quantity": p.quantity, **({"skuId": p.ids["skuId"]} if p.ids.get("skuId") else {})} for p in q.picks]
        await s.call("update_cart", {"items": items, "selectedAddressId": q.address_id})
        bill = await s.call("get_cart")
        payment = await s.call("get_payment_options")
        raw = f"{bill}\n\nPayment options:\n{payment}"
    read = await _read(nova, raw, "the bill total, fees and whether cash on delivery is offered", "report_bill", _BILL_SCHEMA)
    total = read.get("total")
    if not isinstance(total, int | float) or total <= 0:
        raise OrderError(f"I couldn't read the {s.provider.name} bill. Check the cart in the app.")
    return {"total": float(total), "fees": float(read.get("fees") or 0), "cod": bool(read.get("cod_available"))}


# ---------- drafts ----------

def _draft_key(user: str) -> str:
    return f"{user}#order-draft"


def _compare_key(user: str) -> str:
    return f"{user}#price-compare"


def save_comparison(store: Store, user: str, quotes: list[Quote]) -> None:
    store.save_record(_compare_key(user), {"at": time.time(), "quotes": [asdict(q) for q in quotes]})


def saved_quote(store: Store, user: str, app: str, items: list[str]) -> Quote | None:
    saved = store.load_record(_compare_key(user))
    if not saved or time.time() - saved["at"] > COMPARE_SECONDS:
        return None
    for q in saved["quotes"]:
        if q["app"] == app and sorted(p["item"] for p in q["picks"]) == sorted(items):
            return Quote(app=q["app"], address_id=q["address_id"], address=q["address"], picks=[Pick(**p) for p in q["picks"]], missing=q["missing"])
    return None


def save_draft(store: Store, user: str, q: Quote, bill: dict[str, Any]) -> dict[str, Any]:
    turn = current_turn.get() or {}
    draft = {
        "id": uuid.uuid4().hex[:12], "app": q.app, "quote": asdict(q), "total": bill["total"], "fees": bill["fees"],
        "payment": "cod" if bill["cod"] else "online", "created": time.time(), "turn": turn.get("id", ""),
    }
    store.save_record(_draft_key(user), draft)
    return draft


def confirmed_draft(store: Store, user: str) -> dict[str, Any]:
    """The read-back the user is confirming right now, or OrderError explaining what's missing."""
    turn = current_turn.get()
    if not turn:
        raise OrderError("Orders can only be placed from the Pantry Pilot app, after you confirm out loud.")
    draft = store.load_record(_draft_key(user))
    if not draft or time.time() - draft["created"] > DRAFT_SECONDS:
        raise OrderError("There's no order waiting for confirmation. Ask me to prepare it first.")
    if draft["turn"] == turn["id"]:
        raise OrderError("Please check the items and total first, then say “place the order”.")
    if not CONFIRM.search(turn.get("text", "")):
        raise OrderError("To place it, say “place the order”.")
    return draft


def clear_draft(store: Store, user: str) -> None:
    store.delete_record(_draft_key(user))


def readback(q: Quote, bill: dict[str, Any], payment: str, provider: connectors.Provider) -> str:
    items = ", ".join(f"{p.name}{f' {p.pack}' if p.pack else ''}{f' ×{p.quantity}' if p.quantity > 1 else ''}" for p in q.picks)
    pay = "cash on delivery" if payment == "cod" else f"paid online through a {provider.name} payment link I'll send you"
    fees = f" including {rupees(bill['fees'])} fees" if bill.get("fees") else ""
    missing = f" I couldn't find {', '.join(q.missing)}." if q.missing else ""
    return (
        f"{provider.name} order to {q.address}: {items}. Total {rupees(bill['total'])}{fees}, {pay}.{missing} "
        "Say “place the order” to confirm."
    )


_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "placed": {"type": "boolean", "description": "The app confirmed the order was placed."},
        "order_id": {"type": "string"},
        "total": {"type": "number", "description": "Order total in rupees, if stated."},
        "payment_url": {"type": "string", "description": "The https payment link, if the app gave one."},
        "message": {"type": "string", "description": "The app's own confirmation or error message."},
    },
    "required": ["placed"],
}


async def place(s: AppSession, nova: Nova, draft: dict[str, Any], user: str) -> dict[str, Any]:
    """Re-check the live total, then place the order. Returns {placed, order_id, payment_url, message}."""
    q = Quote(**{**draft["quote"], "picks": [Pick(**p) for p in draft["quote"]["picks"]]})
    if s.provider.id == "zepto":
        await s.call("select_saved_address", {"addressId": q.address_id})
    bill = await _fill_and_bill(s, nova, q, user)
    if abs(bill["total"] - draft["total"]) > 1:
        raise OrderError(
            f"The {s.provider.name} total changed from {rupees(draft['total'])} to {rupees(bill['total'])}. "
            "Ask me to prepare the order again."
        )
    if s.provider.id == "zepto":
        tool = "create_order" if draft["payment"] == "cod" else "create_online_payment_order"
        raw = await s.call(tool, {"confirmOrder": True, "userAddressId": q.address_id})
    else:
        if draft["payment"] != "cod":
            raise OrderError("Swiggy only takes cash on delivery from Pantry Pilot; finish online payment in the Swiggy app.")
        raw = await s.call("checkout", {"addressId": q.address_id, "paymentMethod": "Cash"})
    read = await _read(nova, raw, "whether the order was placed", "report_result", _RESULT_SCHEMA)
    link = str(read.get("payment_url") or "")
    return {
        "placed": bool(read.get("placed")),
        "order_id": _verified(read.get("order_id"), raw) or "",
        "payment_url": link if link.startswith("https://") and link in raw else "",
        "message": str(read.get("message") or "")[:300],
        "total": bill["total"],
    }
