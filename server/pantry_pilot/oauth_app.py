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


def start(store: Store, params: dict[str, str], clients: dict[str, str]) -> str:
    """Validate an authorization request from a first-party client; returns the request id."""
    client_id = params.get("client_id", "")
    if client_id not in clients:
        raise OAuthError("invalid_client", "Unknown app.")
    redirect_uri = params.get("redirect_uri", "")
    if redirect_uri not in REDIRECTS:
        raise OAuthError("invalid_request", "That app's return address isn't allowed.")
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only the authorization code flow is supported.")
    if not params.get("code_challenge") or params.get("code_challenge_method", "S256") != "S256":
        raise OAuthError("invalid_request", "PKCE with S256 is required.")
    request_id = secrets.token_urlsafe(18)
    store.save_record(_request_key(request_id), {
        "client_id": client_id,
        "client_name": clients[client_id],
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
