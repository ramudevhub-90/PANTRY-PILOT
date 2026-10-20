"""Kroger (US): find grocery-list items in Kroger's catalog and add the ones the user picks to
their own Kroger cart. The user checks out on kroger.com; Pantry Pilot never orders or pays.

Kroger's Acceptable Use rules shape this module:
- Only items the user explicitly chooses go in the cart (the web app shows a review step, and the
  Alexa tool must read the matches back and hear a yes first).
- Product data is shown exactly as returned, and never used to compare prices with other stores.
- Nothing about the customer's location, searches or cart contents is stored. Only the OAuth
  tokens are kept, so the user doesn't have to sign in again.

Credentials: KROGER_CLIENT_ID / KROGER_CLIENT_SECRET locally, or the SSM SecureString named by
KROGER_CREDENTIALS_PARAM ({"clientId": ..., "clientSecret": ...}) on Lambda.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .store import Store

HOSTS = {
    "production": "https://api.kroger.com/v1",
    "certification": "https://api-ce.kroger.com/v1",
}
CUSTOMER_SCOPE = "cart.basic:write"
CATALOG_SCOPE = "product.compact"
PENDING_SECONDS = 900
CREDENTIALS_CACHE_SECONDS = 300
MATCHES_PER_ITEM = 3
CART_URL = "https://www.kroger.com/cart"

Modality = Literal["PICKUP", "DELIVERY"]


class KrogerError(Exception):
    """A message that can be shown to the user as is."""


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str

    @property
    def basic(self) -> str:
        return "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()


def callback_path() -> str:
    return "/api/kroger/callback"


def _tokens_key(user: str) -> str:
    return f"{user}#kroger-tokens"


def _pending_key(user: str) -> str:
    return f"{user}#kroger-pending"


def _state_key(state: str) -> str:
    return f"kroger-state#{state}"


class Kroger:
    def __init__(self, environment: str, credentials: Callable[[], Credentials | None]) -> None:
        self.environment = environment if environment in HOSTS else "production"
        self._credentials = credentials
        self._catalog_token: tuple[str, float] | None = None

    @property
    def base(self) -> str:
        return HOSTS[self.environment]

    @property
    def enabled(self) -> bool:
        return self._credentials() is not None

    def _creds(self) -> Credentials:
        if not (creds := self._credentials()):
            raise KrogerError("Kroger isn't set up yet.")
        return creds

    # ---------- the customer's sign-in (authorization code + PKCE) ----------

    def connected(self, store: Store, user: str) -> bool:
        return bool(store.load_record(_tokens_key(user)))

    def start_sign_in(self, store: Store, user: str, redirect_uri: str, return_to: str) -> str:
        creds = self._creds()
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        now = time.time()
        store.save_record(_pending_key(user), {"verifier": verifier, "state": state, "created": now, "redirect_uri": redirect_uri})
        store.save_record(_state_key(state), {"user": user, "created": now, "return_to": return_to})
        query = {
            "scope": CUSTOMER_SCOPE,
            "response_type": "code",
            "client_id": creds.client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        return f"{self.base}/connect/oauth2/authorize?{urllib.parse.urlencode(query)}"

    def finish_sign_in(self, store: Store, state: str, code: str) -> str:
        """Kroger sent the browser back with ?code&state. Returns where to send the user next."""
        started = store.load_record(_state_key(state)) if state else None
        if not started:
            raise KrogerError("That Kroger sign-in link has expired. Start again from Pantry Pilot.")
        store.delete_record(_state_key(state))
        user = started["user"]
        pending = store.load_record(_pending_key(user))
        if not pending or pending["state"] != state or time.time() - pending["created"] > PENDING_SECONDS:
            raise KrogerError("That Kroger sign-in link has expired. Start again from Pantry Pilot.")
        store.delete_record(_pending_key(user))
        token = self._token_request({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending["redirect_uri"],
            "code_verifier": pending["verifier"],
        })
        self._save_tokens(store, user, token)
        return started.get("return_to") or "/"

    def disconnect(self, store: Store, user: str) -> None:
        store.delete_record(_tokens_key(user))

    def _token_request(self, form: dict[str, str]) -> dict[str, Any]:
        status, token = _request(
            "POST",
            f"{self.base}/connect/oauth2/token",
            urllib.parse.urlencode(form).encode(),
            {"Authorization": self._creds().basic, "Content-Type": "application/x-www-form-urlencoded"},
        )
        if status != 200 or "access_token" not in token:
            raise KrogerError("Kroger didn't accept the sign-in. Try connecting again.")
        return token

    def _save_tokens(self, store: Store, user: str, token: dict[str, Any]) -> None:
        store.save_record(_tokens_key(user), {
            "access_token": token["access_token"],
            "refresh_token": token.get("refresh_token"),
            "expires_at": time.time() + int(token.get("expires_in", 1800)) - 60,
        })

    def _customer_token(self, store: Store, user: str) -> str:
        saved = store.load_record(_tokens_key(user))
        if not saved:
            raise KrogerError("Connect your Kroger account first.")
        if time.time() < saved["expires_at"]:
            return saved["access_token"]
        if not saved.get("refresh_token"):
            self.disconnect(store, user)
            raise KrogerError("Your Kroger sign-in expired. Connect Kroger again.")
        try:
            token = self._token_request({"grant_type": "refresh_token", "refresh_token": saved["refresh_token"]})
        except KrogerError:
            self.disconnect(store, user)
            raise KrogerError("Your Kroger sign-in expired. Connect Kroger again.") from None
        self._save_tokens(store, user, token)
        return token["access_token"]

    # ---------- catalog (client credentials) ----------

    def _catalog(self) -> str:
        if self._catalog_token and time.time() < self._catalog_token[1]:
            return self._catalog_token[0]
        token = self._token_request({"grant_type": "client_credentials", "scope": CATALOG_SCOPE})
        self._catalog_token = (token["access_token"], time.time() + int(token.get("expires_in", 1800)) - 60)
        return token["access_token"]

    def search(self, term: str) -> list[dict[str, Any]]:
        """Up to three Kroger products for one grocery-list item, as Kroger describes them."""
        term = term.strip()
        if len(term) < 3:
            return []
        query = urllib.parse.urlencode({"filter.term": term, "filter.limit": MATCHES_PER_ITEM})
        status, body = _request("GET", f"{self.base}/products?{query}", None, {"Authorization": f"Bearer {self._catalog()}"})
        if status == 401:
            self._catalog_token = None
        if status != 200:
            raise KrogerError("Kroger's catalog isn't answering right now. Try again in a minute.")
        return [p for p in (_product(raw) for raw in body.get("data", [])) if p]

    # ---------- the customer's cart ----------

    def add_to_cart(self, store: Store, user: str, items: list[dict[str, Any]], modality: Modality = "PICKUP") -> int:
        """Adds exactly the items the user chose ({upc, quantity}). Returns how many."""
        chosen = [
            {"upc": str(i["upc"]), "quantity": max(1, min(int(i.get("quantity", 1)), 24)), "modality": modality}
            for i in items
            if str(i.get("upc", "")).isdigit()
        ]
        if not chosen:
            raise KrogerError("Pick at least one product to add.")
        token = self._customer_token(store, user)
        status, _ = _request(
            "PUT",
            f"{self.base}/cart/add",
            json.dumps({"items": chosen}).encode(),
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if status == 401:
            self.disconnect(store, user)
            raise KrogerError("Your Kroger sign-in expired. Connect Kroger again.")
        if status not in (200, 204):
            raise KrogerError("Kroger couldn't add those items right now. Try again in a minute.")
        return len(chosen)


def _product(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not raw.get("upc") or not raw.get("description"):
        return None
    image = None
    for picture in raw.get("images", []):
        if picture.get("perspective") == "front":
            sizes = {s.get("size"): s.get("url") for s in picture.get("sizes", [])}
            image = sizes.get("thumbnail") or sizes.get("small") or sizes.get("medium")
            break
    items = raw.get("items") or [{}]
    return {
        "upc": raw["upc"],
        "description": raw["description"],
        "brand": raw.get("brand"),
        "size": items[0].get("size"),
        "image": image,
    }


def _request(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=15) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
    except urllib.error.URLError:
        return 0, {}


def _ssm_credentials(name: str) -> Callable[[], Credentials | None]:
    """Reads the SecureString every few minutes, so credentials added later are picked up."""
    cache: dict[str, Any] = {"at": 0.0, "value": None}

    def read() -> Credentials | None:
        if time.time() - cache["at"] < CREDENTIALS_CACHE_SECONDS:
            return cache["value"]
        import boto3

        client = boto3.client("ssm")
        try:
            raw = json.loads(client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"])
            cache["value"] = Credentials(raw["clientId"].strip(), raw["clientSecret"].strip())
        except (client.exceptions.ParameterNotFound, ValueError, KeyError):
            cache["value"] = None
        except Exception as err:  # no AWS access (e.g. local credentials): Kroger stays off
            print(f"Kroger credentials unavailable: {type(err).__name__}")
            cache["value"] = None
        cache["at"] = time.time()
        return cache["value"]

    return read


def kroger_from_env() -> Kroger:
    environment = os.environ.get("KROGER_ENV", "production")
    client_id, secret = os.environ.get("KROGER_CLIENT_ID"), os.environ.get("KROGER_CLIENT_SECRET")
    if client_id and secret:
        creds = Credentials(client_id, secret)
        return Kroger(environment, lambda: creds)
    if param := os.environ.get("KROGER_CREDENTIALS_PARAM"):
        return Kroger(environment, _ssm_credentials(param))
    return Kroger(environment, lambda: None)
