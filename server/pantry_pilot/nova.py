"""Amazon Nova on Bedrock: reads grocery photos, bills and videos, and drives the agent."""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol

from .meal_ideas import SUGGEST_TOOL
from .models import UNITS, Category, DetectedItem

CATEGORIES: tuple[Category, ...] = ("produce", "dairy", "protein", "grains", "pantry", "condiments")

# MIME type -> (Converse content block type, format)
_MEDIA_FORMATS: dict[str, tuple[str, str]] = {
    "image/jpeg": ("image", "jpeg"),
    "image/png": ("image", "png"),
    "image/webp": ("image", "webp"),
    "image/gif": ("image", "gif"),
    "video/mp4": ("video", "mp4"),
    "video/quicktime": ("video", "mov"),
    "video/webm": ("video", "webm"),
    "application/pdf": ("document", "pdf"),
}


@dataclass(frozen=True)
class Media:
    kind: str  # "image" | "video" | "document"
    format: str
    data: bytes


def parse_data_url(url: str) -> Media:
    match = re.match(r"data:([\w/+.-]+);base64,(.+)$", url, re.S)
    if not match:
        raise ValueError("Expected a base64 data URL")
    mime, payload = match.groups()
    if mime not in _MEDIA_FORMATS:
        raise ValueError(f"Unsupported media type: {mime}")
    kind, fmt = _MEDIA_FORMATS[mime]
    return Media(kind, fmt, base64.b64decode(payload))


def _content_block(media: Media, index: int) -> dict[str, Any]:
    if media.kind == "document":
        return {"document": {"format": media.format, "name": f"bill-{index + 1}", "source": {"bytes": media.data}}}
    return {media.kind: {"format": media.format, "source": {"bytes": media.data}}}


DETECT_PROMPT = """You read photos, videos and bills of groceries for a home pantry app.
Report every food or kitchen grocery item you can identify by calling report_items.

Rules:
- Only report items you can actually see or read. If the media shows no food, groceries or bill
  lines (a blank image, a person, a room), call report_items with an empty list. Never guess.
- Bills, receipts and order screenshots: list each grocery line item. Skip non-food products
  (toiletries, medicine, electronics, clothing), fees, taxes, discounts, totals, freebies,
  vouchers and promotional lines. Skip anything marked cancelled or refunded.
- Combine pack size and count into one total: "Paneer 200 g x 2" is 400 g. Prefer kg or L
  when the total is 1000 g or 1000 ml or more.
- Photos and videos: count separate items; estimate loose produce by weight. List each product
  once even if it appears in several photos or video frames.
- Use plain English names a person would say ("Tomatoes", "Paneer", "Moong dal"), no brands.
- expires_in_days: typical shelf life from today when stored properly at home.
- confidence: how sure you are about the item AND its quantity. Use 0.9+ only when the
  quantity is printed or clearly countable; use 0.5-0.8 for estimates.
- Pick one fitting emoji per item."""

_REPORT_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "report_items",
        "description": "Report the grocery items found in the provided media.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "emoji": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit": {"type": "string", "enum": list(UNITS)},
                                "category": {"type": "string", "enum": list(CATEGORIES)},
                                "expires_in_days": {"type": "integer"},
                                "confidence": {"type": "number"},
                            },
                            "required": ["name", "quantity", "unit", "category", "expires_in_days", "confidence"],
                        },
                    }
                },
                "required": ["items"],
            }
        },
    }
}


ORDERS_PROMPT = """You read order data returned by grocery apps (Swiggy Instamart, Zepto) for a home
pantry app. Report every order you can see by calling report_orders.

Rules:
- order_id: the app's order id exactly as given. status: "delivered", "cancelled", "in_progress" or "other".
- items: only food and kitchen groceries. Skip non-food products (toiletries, medicine, electronics,
  clothing, household goods), fees, taxes, discounts, totals, freebies, vouchers and promotional lines.
  Leave items empty when the data lists no items for an order.
- Combine pack size and count into one total: "Paneer 200 g x 2" is 400 g. Prefer kg or L for 1000 g or ml and up.
- Plain English names a person would say ("Paneer", "Moong dal", "Ponni rice"), no brands.
- expires_in_days: typical shelf life from delivery when stored properly at home.
- confidence: 0.95 when the quantity is printed in the data, lower if you had to guess."""

_ORDERS_TOOL: dict[str, Any] = {
    "toolSpec": {
        "name": "report_orders",
        "description": "Report the orders and their grocery items.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "orders": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "order_id": {"type": "string"},
                                "status": {"type": "string", "enum": ["delivered", "cancelled", "in_progress", "other"]},
                                "items": _REPORT_TOOL["toolSpec"]["inputSchema"]["json"]["properties"]["items"],
                            },
                            "required": ["order_id", "status", "items"],
                        },
                    }
                },
                "required": ["orders"],
            }
        },
    }
}


@dataclass
class OrderRead:
    order_id: str
    status: str
    items: list[DetectedItem]


def _clean_orders(raw_orders: list[dict[str, Any]]) -> list[OrderRead]:
    orders = []
    for o in raw_orders:
        order_id = str(o.get("order_id", "")).strip()
        if not order_id:
            continue
        items = [d for d in (_clean_detection(i) for i in o.get("items") or []) if d]
        orders.append(OrderRead(order_id, str(o.get("status", "other")), items))
    return orders


class NovaUnavailable(RuntimeError):
    """Bedrock refused the call (no quota, no access, throttled)."""


class Nova(Protocol):
    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]: ...

    def detect_items(self, media: list[Media], kind: str) -> list[DetectedItem]: ...

    def read_orders(self, raw: list[str]) -> list[OrderRead]: ...

    def suggest_meals(self, system: str, request: str) -> list[dict[str, Any]]: ...


def _clean_detection(raw: dict[str, Any]) -> DetectedItem | None:
    """Coerce model output into a valid DetectedItem, dropping anything unusable."""
    name = str(raw.get("name", "")).strip()
    try:
        quantity = float(raw.get("quantity", 0))
    except (TypeError, ValueError):
        return None
    if not name or quantity <= 0:
        return None
    category = raw.get("category")
    unit = raw.get("unit") if raw.get("unit") in UNITS else "pcs"
    # The model sometimes reports 1020 g; people say 1.02 kg.
    if unit in ("g", "ml") and quantity >= 1000:
        quantity, unit = quantity / 1000, "kg" if unit == "g" else "L"
    return DetectedItem(
        name=name[:60],
        emoji=str(raw.get("emoji") or "🛒")[:4],
        quantity=round(quantity, 2),
        unit=unit,
        category=category if category in CATEGORIES else "pantry",
        expires_in_days=max(0, int(raw.get("expires_in_days") or 7)),
        confidence=min(1.0, max(0.0, float(raw.get("confidence") or 0.5))),
    )


def _assumed_role_session(role_arn: str, region: str) -> Any:
    """A boto3 session that assumes `role_arn` and refreshes its credentials before they expire."""
    import boto3
    from botocore.credentials import AssumeRoleCredentialFetcher, DeferredRefreshableCredentials
    from botocore.session import get_session

    source = get_session()
    fetcher = AssumeRoleCredentialFetcher(
        client_creator=source.create_client,
        source_credentials=source.get_credentials(),
        role_arn=role_arn,
        extra_args={"RoleSessionName": "pantry-pilot"},
    )
    session = get_session()
    session._credentials = DeferredRefreshableCredentials(method="assume-role", refresh_using=fetcher.fetch_credentials)
    session.set_config_variable("region", region)
    return boto3.Session(botocore_session=session)


class BedrockNova:
    def __init__(self, model_id: str | None = None, region: str | None = None) -> None:
        import boto3

        self.model_id = model_id or os.environ.get("NOVA_MODEL", "global.amazon.nova-2-lite-v1:0")
        region = region or os.environ.get("AWS_REGION", "us-east-1")
        # NOVA_ROLE_ARN: call Nova from another account while this one's Bedrock quota is 0.
        role_arn = os.environ.get("NOVA_ROLE_ARN")
        session = _assumed_role_session(role_arn, region) if role_arn else boto3.Session(region_name=region)
        self._client = session.client("bedrock-runtime")

    def converse(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "system": [{"text": system}],
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.3},
        }
        if tools:
            kwargs["toolConfig"] = {"tools": tools, **({"toolChoice": tool_choice} if tool_choice else {})}
        try:
            return self._client.converse(**kwargs)
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "")
            if code in {"ThrottlingException", "AccessDeniedException", "ServiceQuotaExceededException"}:
                detail = err.response["Error"].get("Message", code)
                raise NovaUnavailable(f"Amazon Nova is unavailable right now ({detail}).") from err
            raise

    def detect_items(self, media: list[Media], kind: str) -> list[DetectedItem]:
        content = [_content_block(m, i) for i, m in enumerate(media)]
        content.append({"text": f"Source type: {kind}. Report every grocery item."})
        response = self.converse(
            system=DETECT_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=[_REPORT_TOOL],
            tool_choice={"tool": {"name": "report_items"}},
            max_tokens=4096,
        )
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                items = block["toolUse"]["input"].get("items", [])
                return [d for d in (_clean_detection(i) for i in items) if d]
        return []

    def suggest_meals(self, system: str, request: str) -> list[dict[str, Any]]:
        """Dishes invented for the pantry (see meal_ideas), as raw suggest_meals tool input."""
        response = self.converse(
            system=system,
            messages=[{"role": "user", "content": [{"text": request}]}],
            tools=[SUGGEST_TOOL],
            tool_choice={"tool": {"name": "suggest_meals"}},
            max_tokens=3000,
        )
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                return list(block["toolUse"]["input"].get("meals") or [])
        return []

    def read_orders(self, raw: list[str]) -> list[OrderRead]:
        """Turn whatever a grocery app's order tools returned into orders with clean pantry items."""
        content = [{"text": f"--- Data {i + 1} ---\n{text[:60_000]}"} for i, text in enumerate(raw)]
        response = self.converse(
            system=ORDERS_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=[_ORDERS_TOOL],
            tool_choice={"tool": {"name": "report_orders"}},
            max_tokens=4096,
        )
        for block in response["output"]["message"]["content"]:
            if "toolUse" in block:
                return _clean_orders(block["toolUse"]["input"].get("orders", []))
        return []


_MOCK_DETECTIONS: list[dict[str, Any]] = [
    {"name": "Tomatoes", "emoji": "🍅", "quantity": 1, "unit": "kg", "category": "produce", "expires_in_days": 6, "confidence": 0.62},
    {"name": "Onions", "emoji": "🧅", "quantity": 2, "unit": "kg", "category": "produce", "expires_in_days": 25, "confidence": 0.91},
    {"name": "Eggs", "emoji": "🥚", "quantity": 12, "unit": "pcs", "category": "protein", "expires_in_days": 14, "confidence": 0.96},
    {"name": "Coriander", "emoji": "🌿", "quantity": 1, "unit": "bunch", "category": "produce", "expires_in_days": 4, "confidence": 0.84},
    {"name": "Paneer", "emoji": "🧀", "quantity": 200, "unit": "g", "category": "dairy", "expires_in_days": 5, "confidence": 0.97},
    {"name": "Bananas", "emoji": "🍌", "quantity": 6, "unit": "pcs", "category": "produce", "expires_in_days": 5, "confidence": 0.88},
]


class MockNova:
    """Offline stand-in for development while Bedrock quota is unavailable. Not used in production."""

    def converse(self, **_: Any) -> dict[str, Any]:
        raise NovaUnavailable(
            "Chat needs Amazon Nova, which this server is simulating for now. Photo scans and the dashboard still work."
        )

    def detect_items(self, media: list[Media], kind: str) -> list[DetectedItem]:
        return [d for d in (_clean_detection(i) for i in _MOCK_DETECTIONS) if d]

    def suggest_meals(self, system: str, request: str) -> list[dict[str, Any]]:
        raise NovaUnavailable("Meal ideas need Amazon Nova; using the built-in recipes.")

    def read_orders(self, raw: list[str]) -> list[OrderRead]:
        # Offline stand-in: accept data already shaped like report_orders' input ({"orders": [...]}).
        orders: list[dict[str, Any]] = []
        for text in raw:
            try:
                orders += json.loads(text).get("orders", [])
            except (ValueError, AttributeError):
                continue
        return _clean_orders(orders)


def nova_from_env() -> Nova:
    return MockNova() if os.environ.get("NOVA_MODE") == "mock" else BedrockNova()
