"""Sign-in for outside MCP clients (Claude) through Pantry Pilot's own sign-in page.

Cognito's hosted login only offers email + password, and Pantry Pilot accounts sign in with an
emailed code. So for first-party clients (FIRST_PARTY_CLIENTS) Pantry Pilot runs the OAuth
authorization-code + PKCE flow itself:

1. /oauth/authorize checks the client and its redirect, remembers the request and sends the browser
   to the app (/?oauth=<request>), which shows the normal sign-in and then a consent card.
2. On "Allow" the app posts its refresh token; the server checks it belongs to the signed-in person
   and returns the client's redirect with a one-time code.
3. /oauth/token swaps the code (with the PKCE verifier) for a fresh Cognito access token and the
   refresh token; later refreshes go through Cognito too. Tokens are the web app's own, so /mcp
   accepts them like the app's API does.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from .store import Store

REDIRECTS = frozenset({
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    # MCP Inspector and local scripts.
    "http://localhost:6274/oauth/callback",
    "http://localhost/callback",
    "http://127.0.0.1/callback",
})
REQUEST_SECONDS = 15 * 60
CODE_SECONDS = 5 * 60


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


Refresh = Callable[[str], dict[str, Any]]
"""Cognito REFRESH_TOKEN_AUTH: refresh token -> {"AccessToken", "ExpiresIn"}; raises on a bad token."""


def _request_key(request_id: str) -> str:
    return f"oauth-request#{request_id}"


def _code_key(code: str) -> str:
    return f"oauth-code#{hashlib.sha256(code.encode()).hexdigest()}"


# ---------- Dynamic Client Registration (RFC 7591): an MCP client registers with just our URL ----------

def _client_key(client_id: str) -> str:
    return f"oauth-dcr#{client_id}"


def _allowed_redirect(uri: str) -> bool:
    parsed = urllib.parse.urlparse(uri)
    local = parsed.hostname in ("localhost", "127.0.0.1")
    return bool(parsed.netloc) and not parsed.fragment and (parsed.scheme == "https" or (parsed.scheme == "http" and local))


def register(store: Store, body: dict[str, Any]) -> dict[str, Any]:
    """Register a public client (PKCE, no secret) for the given https (or localhost) redirect URIs."""
    redirects = body.get("redirect_uris")
    if not isinstance(redirects, list) or not 1 <= len(redirects) <= 5 or not all(isinstance(u, str) and _allowed_redirect(u) for u in redirects):
        raise OAuthError("invalid_redirect_uri", "Give 1 to 5 https redirect URIs (http only for localhost).")
    if body.get("token_endpoint_auth_method", "none") != "none":
        raise OAuthError("invalid_client_metadata", "Only public clients (token_endpoint_auth_method none) are supported.")
    name = str(body.get("client_name") or "An app")[:60]
    client_id = "pp-" + secrets.token_urlsafe(18)
    now = int(time.time())
    store.save_record(_client_key(client_id), {"name": name, "redirect_uris": redirects, "created": now})
    return {
        "client_id": client_id,
        "client_id_issued_at": now,
        "client_name": name,
        "redirect_uris": redirects,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def client(store: Store, client_id: str, first_party: dict[str, str]) -> dict[str, Any] | None:
    """{"name", "redirect_uris", "verified"} for a client we sign in ourselves, else None."""
    if client_id in first_party:
        return {"name": first_party[client_id], "redirect_uris": sorted(REDIRECTS), "verified": True}
    if client_id.startswith("pp-") and (record := store.load_record(_client_key(client_id))):
        return {"name": record["name"], "redirect_uris": record["redirect_uris"], "verified": False}
    return None


def start(store: Store, params: dict[str, str], clients: dict[str, str]) -> str:
    """Validate an authorization request from a client we sign in ourselves; returns the request id."""
    client_id = params.get("client_id", "")
    known = client(store, client_id, clients)
    if not known:
        raise OAuthError("invalid_client", "Unknown app.")
    redirect_uri = params.get("redirect_uri", "")
    if redirect_uri not in known["redirect_uris"]:
        raise OAuthError("invalid_request", "That app's return address isn't allowed.")
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only the authorization code flow is supported.")
    if not params.get("code_challenge") or params.get("code_challenge_method", "S256") != "S256":
        raise OAuthError("invalid_request", "PKCE with S256 is required.")
    request_id = secrets.token_urlsafe(18)
    store.save_record(_request_key(request_id), {
        "client_id": client_id,
        "client_name": known["name"],
        "verified": known["verified"],
        "redirect_uri": redirect_uri,
        "state": params.get("state", ""),
        "challenge": params["code_challenge"],
        "created": time.time(),
    })
    return request_id


def pending(store: Store, request_id: str) -> dict[str, Any]:
    request = store.load_record(_request_key(request_id))
    if not request or time.time() - request["created"] > REQUEST_SECONDS:
        raise OAuthError("invalid_request", "This sign-in link has expired. Start again from the app you're connecting.")
    return request


def _redirect(request: dict[str, Any], **params: str) -> str:
    query = {**params, **({"state": request["state"]} if request.get("state") else {})}
    return f"{request['redirect_uri']}?{urllib.parse.urlencode(query)}"


def approve(store: Store, request_id: str, user: str, refresh_token: str, refresh: Refresh, subject: Callable[[str], str]) -> str:
    """The signed-in person allowed the app; returns the app's redirect with a one-time code."""
    request = pending(store, request_id)
    try:
        fresh = refresh(refresh_token)
    except Exception:
        raise OAuthError("invalid_grant", "Your sign-in has expired. Sign in again.") from None
    if subject(fresh["AccessToken"]) != user:
        raise OAuthError("invalid_grant", "That sign-in belongs to someone else.", 403)
    code = secrets.token_urlsafe(32)
    store.save_record(_code_key(code), {**request, "refresh_token": refresh_token, "user": user, "issued": time.time()})
    store.delete_record(_request_key(request_id))
    return _redirect(request, code=code)


def deny(store: Store, request_id: str) -> str:
    request = pending(store, request_id)
    store.delete_record(_request_key(request_id))
    return _redirect(request, error="access_denied", error_description="The user declined.")


def _pkce_ok(verifier: str, challenge: str) -> bool:
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(digest, challenge)


def token(store: Store, form: dict[str, str], client_id: str, refresh: Refresh) -> dict[str, Any]:
    """The token endpoint for first-party clients: authorization_code and refresh_token grants."""
    grant = form.get("grant_type")
    if grant == "authorization_code":
        record = store.load_record(_code_key(form.get("code", "")))
        if record:
            store.delete_record(_code_key(form["code"]))  # one use only
        if not record or time.time() - record["issued"] > CODE_SECONDS:
            raise OAuthError("invalid_grant", "The code is invalid or has expired.")
        if record["client_id"] != client_id or record["redirect_uri"] != form.get("redirect_uri"):
            raise OAuthError("invalid_grant", "The code was issued to a different app or address.")
        if not _pkce_ok(form.get("code_verifier", ""), record["challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed.")
        refresh_token = record["refresh_token"]
    elif grant == "refresh_token":
        refresh_token = form.get("refresh_token", "")
    else:
        raise OAuthError("unsupported_grant_type", "Use authorization_code or refresh_token.")
    try:
        fresh = refresh(refresh_token)
    except Exception:
        raise OAuthError("invalid_grant", "The refresh token is invalid or has expired.") from None
    return {
        "access_token": fresh["AccessToken"],
        "token_type": "Bearer",
        "expires_in": int(fresh.get("ExpiresIn", 3600)),
        "refresh_token": refresh_token,
    }


def cognito_refresh(region: str, web_client_id: str) -> Refresh:
    def refresh(refresh_token: str) -> dict[str, Any]:
        import boto3

        result = boto3.client("cognito-idp", region_name=region).initiate_auth(
            AuthFlow="REFRESH_TOKEN_AUTH", ClientId=web_client_id, AuthParameters={"REFRESH_TOKEN": refresh_token}
        )
        return result["AuthenticationResult"]

    return refresh
