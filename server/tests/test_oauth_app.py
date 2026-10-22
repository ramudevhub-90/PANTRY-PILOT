"""Claude signs in on Pantry Pilot's own page: request, consent, one-time code with PKCE, refresh."""

import base64
import hashlib
import urllib.parse

import pytest

from pantry_pilot import oauth_app
from pantry_pilot.store import MemoryStore

CLIENTS = {"claude-id": "Claude"}
VERIFIER = "a-very-long-random-pkce-verifier-string-0123456789"
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def params(**over):
    return {"client_id": "claude-id", "redirect_uri": REDIRECT, "response_type": "code", "state": "s1",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256", **over}


def refresh(token):
    if token != "rt-alice":
        raise ValueError("bad")
    return {"AccessToken": "at-alice", "ExpiresIn": 3600}


def subject(access):
    return {"at-alice": "alice"}[access]


def code_from(url):
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith(REDIRECT) and query["state"] == "s1"
    return query["code"]


def test_full_flow_issues_tokens_once_with_pkce():
    store = MemoryStore()
    request_id = oauth_app.start(store, params(), CLIENTS)
    assert oauth_app.pending(store, request_id)["client_name"] == "Claude"
    code = code_from(oauth_app.approve(store, request_id, "alice", "rt-alice", refresh, subject))

    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": VERIFIER}
    tokens = oauth_app.token(store, form, "claude-id", refresh)
    assert tokens == {"access_token": "at-alice", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "rt-alice"}
    with pytest.raises(oauth_app.OAuthError):  # a code works once
        oauth_app.token(store, form, "claude-id", refresh)
    again = oauth_app.token(store, {"grant_type": "refresh_token", "refresh_token": "rt-alice"}, "claude-id", refresh)
    assert again["access_token"] == "at-alice"


@pytest.mark.parametrize("over", [
    {"client_id": "someone-else"},
    {"redirect_uri": "https://evil.example/cb"},
    {"code_challenge": ""},
    {"code_challenge_method": "plain"},
])
def test_bad_requests_are_refused(over):
    with pytest.raises(oauth_app.OAuthError):
        oauth_app.start(MemoryStore(), params(**over), CLIENTS)


def test_wrong_verifier_or_someone_elses_token_is_refused():
    store = MemoryStore()
    code = code_from(oauth_app.approve(store, oauth_app.start(store, params(), CLIENTS), "alice", "rt-alice", refresh, subject))
    bad = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": "wrong"}
    with pytest.raises(oauth_app.OAuthError, match="PKCE"):
        oauth_app.token(store, bad, "claude-id", refresh)

    request_id = oauth_app.start(store, params(), CLIENTS)
    with pytest.raises(oauth_app.OAuthError, match="someone else"):
        oauth_app.approve(store, request_id, "bob", "rt-alice", refresh, subject)


def test_declining_returns_access_denied():
    store = MemoryStore()
    url = oauth_app.deny(store, oauth_app.start(store, params(), CLIENTS))
    assert "error=access_denied" in url and "state=s1" in url


def test_dynamic_registration_then_sign_in_with_just_the_url():
    store = MemoryStore()
    reg = oauth_app.register(store, {"client_name": "Claude", "redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"})
    cid = reg["client_id"]
    assert cid.startswith("pp-") and reg["token_endpoint_auth_method"] == "none"
    request_id = oauth_app.start(store, params(client_id=cid), {})
    assert oauth_app.pending(store, request_id)["verified"] is False
    code = code_from(oauth_app.approve(store, request_id, "alice", "rt-alice", refresh, subject))
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": VERIFIER}
    assert oauth_app.token(store, form, cid, refresh)["access_token"] == "at-alice"
    with pytest.raises(oauth_app.OAuthError):  # only its own registered redirect
        oauth_app.start(store, params(client_id=cid, redirect_uri="https://claude.com/api/mcp/auth_callback"), {})


@pytest.mark.parametrize("body", [
    {"redirect_uris": ["http://evil.example/cb"]},
    {"redirect_uris": []},
    {"redirect_uris": ["https://a.example/cb#frag"]},
    {"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "client_secret_basic"},
])
def test_registration_rejects_unsafe_metadata(body):
    with pytest.raises(oauth_app.OAuthError):
        oauth_app.register(MemoryStore(), body)
