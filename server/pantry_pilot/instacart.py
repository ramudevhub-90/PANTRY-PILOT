"""Instacart (US): turn the grocery list into an Instacart shopping-list page.

Uses the Instacart Developer Platform API. The user opens the page, picks a store and checks out
on Instacart themselves; Pantry Pilot never places or pays for an order.

The API key comes from INSTACART_API_KEY (local development) or the SSM SecureString named by
INSTACART_KEY_PARAM (Lambda), so it never sits in code or in the function's environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .store import Store

HOSTS = {
    "development": "https://connect.dev.instacart.tools",
    "production": "https://connect.instacart.com",
}
LINK_DAYS = 30
REUSE_SECONDS = 7 * 24 * 3600
KEY_CACHE_SECONDS = 300


class InstacartError(Exception):
    """A message that can be shown to the user as is."""


class Instacart:
    def __init__(self, environment: str, api_key: Callable[[], str | None]) -> None:
        self.environment = environment if environment in HOSTS else "development"
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key())

    def shopping_list_url(self, store: Store, household: str, names: list[str], linkback: str | None = None) -> str:
        """An Instacart page with these items; the same list reuses its link for a week."""
        names = [n.strip() for n in names if n.strip()]
        if not names:
            raise InstacartError("Your grocery list is empty. Add a few items first.")
        key = self._api_key()
        if not key:
            raise InstacartError("Instacart isn't set up yet.")

        fingerprint = hashlib.sha256(json.dumps(sorted(n.lower() for n in names)).encode()).hexdigest()
        record_key = f"{household}#instacart-link"
        saved = store.load_record(record_key)
        if saved and saved["fingerprint"] == fingerprint and time.time() - saved["created"] < REUSE_SECONDS:
            return saved["url"]

        body: dict[str, Any] = {
            "title": "Pantry Pilot grocery list",
            "link_type": "shopping_list",
            "expires_in": LINK_DAYS,
            "line_items": [{"name": n, "display_text": n} for n in names],
        }
        if linkback and linkback.startswith("https://"):
            body["landing_page_configuration"] = {"partner_linkback_url": linkback}
        status, data = _post(f"{HOSTS[self.environment]}/idp/v1/products/products_link", body, key)
        if status != 200 or not data.get("products_link_url"):
            raise InstacartError("Instacart couldn't make the list right now. Try again in a minute.")
        url = data["products_link_url"]
        store.save_record(record_key, {"fingerprint": fingerprint, "url": url, "created": time.time()})
        return url


def _post(url: str, body: dict[str, Any], key: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
    except urllib.error.URLError:
        return 0, {}


def _ssm_key(name: str) -> Callable[[], str | None]:
    """Reads the SecureString once every few minutes, so a key added later is picked up."""
    cache: dict[str, Any] = {"at": 0.0, "value": None}

    def read() -> str | None:
        if time.time() - cache["at"] < KEY_CACHE_SECONDS:
            return cache["value"]
        import boto3

        client = boto3.client("ssm")
        try:
            cache["value"] = client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"].strip() or None
        except client.exceptions.ParameterNotFound:
            cache["value"] = None
        cache["at"] = time.time()
        return cache["value"]

    return read


def instacart_from_env() -> Instacart:
    environment = os.environ.get("INSTACART_ENV", "development")
    if key := os.environ.get("INSTACART_API_KEY"):
        return Instacart(environment, lambda: key)
    if param := os.environ.get("INSTACART_KEY_PARAM"):
        return Instacart(environment, _ssm_key(param))
    return Instacart(environment, lambda: None)
