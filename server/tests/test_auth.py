import io
import time
import urllib.parse
import urllib.request

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from pantry_pilot.app import create_app
from pantry_pilot.auth import AccountDirectory, AuthConfig
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore

CONFIG = AuthConfig(
    region="us-east-1",
    user_pool_id="us-east-1_TEST",
    domain="https://pp.auth.us-east-1.amazoncognito.com",
    web_client_id="web-app",
)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def token(**overrides):
    claims = {
        "iss": CONFIG.issuer,
        "exp": int(time.time()) + 300,
        "token_use": "access",
        "scope": "pantry-pilot/mcp:tools",
        "client_id": "alexa",
        "sub": "user-1",
    } | overrides
    return jwt.encode(claims, KEY, algorithm="RS256")


def web_token(sub="user-1"):
    return token(client_id="web-app", scope="aws.cognito.signin.user.admin", sub=sub)


def auth_header(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture
def client():
    app = create_app(MemoryStore(), MockNova(), "test", auth=CONFIG, signing_key=lambda _: KEY.public_key())
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c


def test_mcp_requires_a_token_and_sends_no_www_authenticate(client):
    response = client.post("/mcp", json=INIT, headers=MCP_HEADERS)
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers


def test_valid_access_token_reaches_the_mcp_server(client):
    response = client.post("/mcp", json=INIT, headers=MCP_HEADERS | {"Authorization": f"Bearer {token()}"})
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "pantry-pilot"


@pytest.mark.parametrize("bad", [
    token(token_use="id"),
    token(scope="openid email"),
    token(iss="https://cognito-idp.us-east-1.amazonaws.com/other"),
    token(exp=int(time.time()) - 10),
    jwt.encode({"iss": CONFIG.issuer, "token_use": "access", "scope": "pantry-pilot/mcp:tools",
                "exp": int(time.time()) + 300}, "a-forged-hmac-key-of-thirty-two-bytes", algorithm="HS256"),
])
def test_rejects_wrong_tokens(client, bad):
    response = client.post("/mcp", json=INIT, headers=MCP_HEADERS | {"Authorization": f"Bearer {bad}"})
    assert response.status_code == 401


class FakeCognito:
    """Just enough of boto3's cognito-idp client for AccountDirectory."""

    class exceptions:
        class UserNotFoundException(Exception):
            pass

        class UsernameExistsException(Exception):
            pass

    def __init__(self, existing=()):
        self.users = {e: {} for e in existing}
        self.calls = []

    def admin_get_user(self, UserPoolId, Username):
        self.calls.append(("get", Username))
        if Username not in self.users:
            raise self.exceptions.UserNotFoundException()

    def admin_create_user(self, UserPoolId, Username, UserAttributes, MessageAction):
        self.calls.append(("create", Username, MessageAction))
        self.users[Username] = {a["Name"]: a["Value"] for a in UserAttributes}

    def admin_set_user_password(self, UserPoolId, Username, Password, Permanent):
        self.calls.append(("password", Username, Permanent, len(Password) > 20))


def test_email_code_sign_in_creates_accounts_server_side():
    fake = FakeCognito(existing={"old@example.com"})
    otp_config = AuthConfig(**{**CONFIG.__dict__, "email_otp": True})
    app = create_app(MemoryStore(), MockNova(), "test", auth=otp_config,
                     signing_key=lambda _: KEY.public_key(), account_directory=AccountDirectory(otp_config, fake))
    with TestClient(app) as c:
        assert c.post("/api/auth/start", json={"email": " New@Example.com "}).json() == {"ok": True}
        assert c.post("/api/auth/start", json={"email": "old@example.com"}).json() == {"ok": True}
        assert c.post("/api/auth/start", json={"email": "not-an-email"}).status_code == 400
        assert c.get("/api/config").json()["auth"]["emailOtp"] is True

    assert fake.users["new@example.com"] == {"email": "new@example.com", "email_verified": "true"}
    assert ("create", "new@example.com", "SUPPRESS") in fake.calls
    assert ("password", "new@example.com", True, True) in fake.calls
    assert not any(call[0] == "create" and call[1] == "old@example.com" for call in fake.calls)


def test_config_is_public_and_names_the_web_client(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["auth"] == {
        "region": "us-east-1", "userPoolId": "us-east-1_TEST", "clientId": "web-app", "emailOtp": False,
    }


def test_web_api_requires_a_web_app_token(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers=auth_header(token())).status_code == 401  # MCP client's token
    assert client.get("/api/state", headers=auth_header(web_token())).status_code == 200


def test_each_user_has_their_own_pantry(client):
    client.post("/api/grocery", json={"add": "saffron"}, headers=auth_header(web_token("alice")))
    alice = client.get("/api/state", headers=auth_header(web_token("alice"))).json()
    bob = client.get("/api/state", headers=auth_header(web_token("bob"))).json()
    assert [g["name"] for g in alice["grocery"]] == ["Saffron"]
    assert bob["grocery"] == []
    assert len(bob["inventory"]) > 0  # new users start with a sample pantry


def test_alexa_sees_the_same_pantry_as_the_web_app(client):
    client.post("/api/pantry", json={"items": [{"name": "Saffron", "quantity": 1, "unit": "g"}]},
                headers=auth_header(web_token("alice")))
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "get_inventory", "arguments": {}}}
    response = client.post("/mcp", json=call, headers=MCP_HEADERS | {"mcp-protocol-version": "2025-11-25"}
                           | auth_header(token(sub="alice")))
    names = [i["name"] for i in response.json()["result"]["structuredContent"]["items"]]
    assert "Saffron" in names


def test_metadata_points_clients_at_our_oauth_proxy(client):
    headers = {"x-forwarded-host": "pantry.example.com"}
    resource = client.get("/.well-known/oauth-protected-resource/mcp", headers=headers).json()
    assert resource["resource"] == "https://pantry.example.com/mcp"
    assert resource["authorization_servers"] == ["https://pantry.example.com"]

    server = client.get("/.well-known/oauth-authorization-server", headers=headers).json()
    assert server["authorization_endpoint"] == "https://pantry.example.com/oauth/authorize"
    assert server["token_endpoint"] == "https://pantry.example.com/oauth/token"
    assert "S256" in server["code_challenge_methods_supported"]


def test_authorize_drops_resource_and_keeps_pkce(client):
    response = client.get(
        "/oauth/authorize",
        params={"response_type": "code", "client_id": "alexa", "code_challenge": "abc",
                "code_challenge_method": "S256", "resource": "https://x/mcp", "state": "s"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = urllib.parse.urlparse(response.headers["location"])
    query = dict(urllib.parse.parse_qsl(location.query))
    assert location.netloc == "pp.auth.us-east-1.amazoncognito.com"
    assert "resource" not in query and query["code_challenge"] == "abc" and query["state"] == "s"


def test_token_proxy_forwards_to_cognito_without_resource(client, monkeypatch):
    sent = {}

    class FakeResponse(io.BytesIO):
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request: urllib.request.Request, timeout: float):
        sent["url"], sent["body"], sent["auth"] = request.full_url, request.data.decode(), request.get_header("Authorization")
        return FakeResponse(b'{"access_token":"t","token_type":"Bearer"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    response = client.post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "c", "code_verifier": "v", "resource": "https://x/mcp"},
        headers={"Authorization": "Basic YWxleGE6c2VjcmV0"},
    )
    assert response.status_code == 200 and response.json()["access_token"] == "t"
    assert sent["url"] == "https://pp.auth.us-east-1.amazoncognito.com/oauth2/token"
    assert "resource" not in sent["body"] and "code_verifier=v" in sent["body"]
    assert sent["auth"] == "Basic YWxleGE6c2VjcmV0"
