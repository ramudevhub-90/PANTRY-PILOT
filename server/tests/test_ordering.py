"""Ordering rules: read-back first, explicit "place the order" in a later turn, live total re-checked."""

import contextlib
import dataclasses
import json

import anyio
import pytest

from pantry_pilot import connectors, ordering
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore

ZEPTO = connectors.PROVIDERS["zepto"]


@dataclasses.dataclass
class Result:
    content: list
    is_error: bool = False
    structured_content: dict | None = None


@dataclasses.dataclass
class Text:
    text: str


class FakeZepto:
    """Plays Zepto's MCP server: one saved address, one milk product, a ₹68 bill (or what `total` says)."""

    def __init__(self, total=68):
        self.calls = []
        self.total = total

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        replies = {
            "list_saved_addresses": "Home (default) addressId=addr-123",
            "select_saved_address": "ok",
            "search_multiple_products": "Amul Taaza Milk 1 L ₹34 productVariantId=pv-9 storeProductId=sp-7",
            "update_cart": "cart updated",
            "get_payment_methods": "COD available",
            "create_order": f"Preview total ₹{self.total}" if not args.get("confirmOrder") else "Order placed! orderId=ORD-55",
        }
        return Result([Text(replies[name])])


class ReadingNova(MockNova):
    """Reads the fake replies the way Nova would."""

    def extract(self, system, text, name, schema):
        if name == "report_address":
            return {"address_id": "addr-123", "label": "Home"}
        if name == "report_picks":
            return {"items": [{"query": "milk", "found": True, "name": "Amul Taaza Milk", "pack": "1 L", "price": 34,
                               "productVariantId": "pv-9", "storeProductId": "sp-7"}]}
        if name == "report_bill":
            return {"total": float(text.split("₹")[1].split()[0]), "fees": 0, "cod_available": True}
        if name == "report_result":
            return {"placed": True, "order_id": "ORD-55", "message": "Order placed!"}
        raise AssertionError(name)


def connected(store, user="alice"):
    store.save_record(f"{user}#connector#zepto", {"access_token": "t", "expires_at": 1e12})


def run(coro_fn, *args):
    return anyio.run(coro_fn, *args)


def turn(text, id_):
    return ordering.current_turn.set({"id": id_, "text": text})


async def prepare(store, app_client, nova):
    @contextlib.asynccontextmanager
    async def factory(provider, token):
        yield app_client

    async with ordering.app_session(store, "alice", ZEPTO, factory) as s:
        q = await ordering.quote(s, nova, ["milk"])
        q.picks[0].quantity = 2
        bill = await ordering._fill_and_bill(s, nova, q, "alice")
    return ordering.save_draft(store, "alice", q, bill), q, bill, factory


def test_readback_then_explicit_confirmation_in_a_later_turn_places_the_order():
    store, nova, app = MemoryStore(), ReadingNova(), FakeZepto()
    connected(store)
    t = turn("order milk from zepto", "turn-1")
    draft, q, bill, factory = run(prepare, store, app, nova)
    assert "Say “place the order”" in ordering.readback(q, bill, draft["payment"], ZEPTO)
    assert not any(c == ("create_order", {"confirmOrder": True, "userAddressId": "addr-123"}) for c in app.calls)

    with pytest.raises(ordering.OrderError, match="check the items"):  # same turn as the read-back
        ordering.confirmed_draft(store, "alice")
    ordering.current_turn.reset(t)

    t = turn("yes", "turn-2")
    with pytest.raises(ordering.OrderError, match="say “place the order”"):
        ordering.confirmed_draft(store, "alice")
    ordering.current_turn.reset(t)

    t = turn("ok place the order", "turn-3")
    confirmed = ordering.confirmed_draft(store, "alice")

    async def go():
        async with ordering.app_session(store, "alice", ZEPTO, factory) as s:
            return await ordering.place(s, nova, confirmed, "alice")

    result = run(go)
    ordering.current_turn.reset(t)
    assert result["placed"] and result["order_id"] == "ORD-55"
    assert ("create_order", {"confirmOrder": True, "userAddressId": "addr-123"}) in app.calls
    cart = next(a for n, a in app.calls if n == "update_cart")
    assert cart["cartItems"][0] == {"productVariantId": "pv-9", "storeProductId": "sp-7", "quantity": 2, "name": "Amul Taaza Milk"}


def test_no_order_outside_the_app_or_when_the_total_changed():
    store, nova = MemoryStore(), ReadingNova()
    connected(store)
    t = turn("order milk", "turn-1")
    draft, *_ , factory = run(prepare, store, FakeZepto(total=68), nova)
    ordering.current_turn.reset(t)

    with pytest.raises(ordering.OrderError, match="only be placed from the Pantry Pilot app"):
        ordering.confirmed_draft(store, "alice")  # e.g. an outside MCP client: no turn

    @contextlib.asynccontextmanager
    async def pricier(provider, token):
        yield FakeZepto(total=90)

    t = turn("place the order", "turn-2")
    confirmed = ordering.confirmed_draft(store, "alice")

    async def go():
        async with ordering.app_session(store, "alice", ZEPTO, pricier) as s:
            return await ordering.place(s, nova, confirmed, "alice")

    with pytest.raises(ordering.OrderError, match="total changed from ₹68 to ₹90"):
        run(go)
    ordering.current_turn.reset(t)


def test_ids_the_app_never_sent_are_rejected():
    class Inventing(ReadingNova):
        def extract(self, system, text, name, schema):
            read = super().extract(system, text, name, schema)
            if name == "report_picks":
                read["items"][0]["productVariantId"] = "pv-INVENTED"
            return read

    store = MemoryStore()
    connected(store)

    @contextlib.asynccontextmanager
    async def factory(provider, token):
        yield FakeZepto()

    async def go():
        async with ordering.app_session(store, "alice", ZEPTO, factory) as s:
            return await ordering.quote(s, Inventing(), ["milk"])

    q = run(go)
    assert q.picks == [] and q.missing == ["milk"]


def test_confirm_phrase():
    assert ordering.CONFIRM.search("Yes, place the order")
    assert ordering.CONFIRM.search("confirm my order please")
    assert not ordering.CONFIRM.search("yes")
    assert not ordering.CONFIRM.search("what's in the order?")
