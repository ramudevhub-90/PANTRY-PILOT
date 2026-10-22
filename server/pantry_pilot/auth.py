"""Sign-in with Amazon Cognito, for both the web app and the /mcp endpoint.

Web app: the browser signs users in against Cognito (email + password, sign-up with an emailed
code) and sends the access token on every /api call.

Alexa+: MCP add-ons connect with OAuth 2.1 (authorization code + PKCE S256, or client credentials
for service calls). Cognito issues the tokens; this module fills the gaps between Cognito and what
MCP clients expect:

- Metadata: Protected Resource Metadata (RFC 9728) and Authorization Server Metadata (RFC 8414)
  served from our own origin, since Cognito only publishes OIDC discovery.
- A thin /oauth/authorize + /oauth/token proxy that drops the RFC 8707 `resource` parameter
  (which Cognito may reject) and forwards everything else, including PKCE, unchanged.
- Token checks: Cognito access tokens carry no `aud`, so we check issuer, RS256 signature,
  token_use=access, and the app client or scopes instead.
- 401s carry no WWW-Authenticate header, per the Alexa+ MCP Toolkit requirements.

Every verified request runs with `current_user` set to the token's subject, so each person (and
the Alexa account they link with the same login) gets their own pantry.
"""

from __future__ import annotations

import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Container
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import anyio
import jwt
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

RESOURCE_SERVER = "pantry-pilot"
USER_SCOPES = (f"{RESOURCE_SERVER}/mcp:tools", f"{RESOURCE_SERVER}/mcp:resources")
SERVICE_SCOPE = f"{RESOURCE_SERVER}/mcp:service"
ALL_SCOPES = (*USER_SCOPES, SERVICE_SCOPE)

current_user: ContextVar[str | None] = ContextVar("current_user", default=None)
"""Cognito `sub` of the signed-in caller for the current request, if any."""


@dataclass(frozen=True)
class AuthConfig:
    region: str
    user_pool_id: str
    domain: str
    """Cognito hosted domain, e.g. https://pantry-pilot-123.auth.us-east-1.amazoncognito.com"""
    web_client_id: str | None = None
    """App client the web app signs in with. When set, /api requires a signed-in user."""
    email_otp: bool = False
    """Whether the pool allows passwordless sign-in with an emailed one-time code."""

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @classmethod
    def from_env(cls) -> AuthConfig | None:
        pool = os.environ.get("COGNITO_USER_POOL_ID")
        if not pool:
            return None
        return cls(
            region=pool.split("_", 1)[0],
            user_pool_id=pool,
            domain=os.environ["COGNITO_DOMAIN"].rstrip("/"),
            web_client_id=os.environ.get("COGNITO_WEB_CLIENT_ID") or None,
            email_otp=os.environ.get("COGNITO_EMAIL_OTP") == "on",
        )


class InvalidToken(Exception):
    pass


def jwks_signing_key(config: AuthConfig) -> Callable[[str], Any]:
    jwks = jwt.PyJWKClient(config.jwks_uri, cache_keys=True, lifespan=3600)
    return lambda token: jwks.get_signing_key_from_jwt(token).key


class TokenVerifier:
    """Checks a Cognito access token. Pass `scopes` (any one is enough) and/or `client_ids`."""

    def __init__(
        self,
        config: AuthConfig,
        signing_key: Callable[[str], Any] | None = None,
        *,
        scopes: Container[str] | None = None,
        client_ids: Container[str] | None = None,
    ) -> None:
        self._config = config
        self._signing_key = signing_key or jwks_signing_key(config)
        self._scopes = scopes
        self._client_ids = client_ids

    def verify(self, token: str) -> dict[str, Any]:
        try:
            claims = jwt.decode(
                token,
                self._signing_key(token),
                algorithms=["RS256"],  # fixed here, never taken from the token header
                issuer=self._config.issuer,
                options={"verify_aud": False, "require": ["exp", "iss", "token_use", "sub"]},
            )
        except jwt.PyJWTError as err:
            raise InvalidToken(str(err)) from err
        if claims.get("token_use") != "access":
            raise InvalidToken("not an access token")
        if self._client_ids is not None and claims.get("client_id") not in self._client_ids:
            raise InvalidToken("token is for a different app")
        if self._scopes is not None and not any(s in self._scopes for s in str(claims.get("scope", "")).split()):
            raise InvalidToken("missing Pantry Pilot scope")
        return claims


def _unauthorized(detail: str) -> JSONResponse:
    # Deliberately no WWW-Authenticate header: Alexa+ doesn't support it.
    return JSONResponse({"error": "invalid_token", "error_description": detail}, status_code=401)


class EitherVerifier:
    """Accepts a token either verifier accepts (e.g. MCP scopes, or the web app's own sign-in)."""

    def __init__(self, *verifiers: TokenVerifier) -> None:
        self._verifiers = verifiers

    def verify(self, token: str) -> dict[str, Any]:
        error: InvalidToken | None = None
        for verifier in self._verifiers:
            try:
                return verifier.verify(token)
            except InvalidToken as err:
                error = err
        raise error or InvalidToken("no verifier")


class BearerAuthMiddleware:
    """Requires a valid token on paths matching `protects`, and sets `current_user` from it."""

    def __init__(self, app: Any, verifier: TokenVerifier | EitherVerifier, protects: Callable[[str], bool]) -> None:
        self.app = app
        self.verifier = verifier
        self.protects = protects

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or not self.protects(scope.get("path", "")):
            await self.app(scope, receive, send)
            return
        header = dict(scope["headers"]).get(b"authorization", b"").decode()
        if not header.lower().startswith("bearer "):
            await _unauthorized("Sign-in required")(scope, receive, send)
            return
        try:
            claims = await anyio.to_thread.run_sync(self.verifier.verify, header[7:].strip())
        except InvalidToken as err:
            await _unauthorized(str(err))(scope, receive, send)
            return
        token = current_user.set(claims["sub"])
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(token)


def is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


# /api/whatsapp/webhook is Meta calling us; it checks Meta's signature instead of a sign-in.
PUBLIC_API_PATHS = {"/api/config", "/api/auth/start", "/api/whatsapp/webhook"}
# A shopping app sends the browser back here after its sign-in; the one-time state names the user.
_CONNECTOR_CALLBACK = re.compile(r"^/api/(connectors/[a-z]+|kroger)/callback$")


def is_private_api_path(path: str) -> bool:
    return path.startswith("/api/") and path not in PUBLIC_API_PATHS and not _CONNECTOR_CALLBACK.match(path)


_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class AccountDirectory:
    """Creates a Cognito account the first time someone asks for an email sign-in code.

    Public sign-up stays off: otherwise anyone could register someone else's email with a password
    and be waiting inside that account. Accounts made here get a random password nobody knows, so
    the only way in is the emailed code (which proves the person owns the address)."""

    def __init__(self, config: AuthConfig, client: Any = None) -> None:
        self._pool = config.user_pool_id
        if client is None:
            import boto3

            client = boto3.client("cognito-idp", region_name=config.region)
        self._cognito = client

    def ensure_account(self, email: str) -> None:
        email = email.strip().lower()
        if not _EMAIL.match(email):
            raise ValueError("Enter a valid email address.")
        try:
            self._cognito.admin_get_user(UserPoolId=self._pool, Username=email)
            return
        except self._cognito.exceptions.UserNotFoundException:
            pass
        try:
            self._cognito.admin_create_user(
                UserPoolId=self._pool,
                Username=email,
                UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
                MessageAction="SUPPRESS",
            )
        except self._cognito.exceptions.UsernameExistsException:
            return  # created by a parallel request
        self._cognito.admin_set_user_password(
            UserPoolId=self._pool, Username=email, Password=_random_password(), Permanent=True
        )


def _random_password() -> str:
    # Meets Cognito's default policy (upper, lower, digit, symbol); never shown to anyone.
    return f"Pp1!{secrets.token_urlsafe(24)}"


def public_base_url(request: Request) -> str:
    """Our public origin. Behind CloudFront the Host header is the Lambda URL's, so a CloudFront
    Function copies the viewer's host into x-forwarded-host (trusted: the origin secret is checked first)."""
    if forwarded := request.headers.get("x-forwarded-host"):
        return f"https://{forwarded}"
    return f"{request.url.scheme}://{request.headers['host']}"


def protected_resource_metadata(request: Request) -> JSONResponse:
    base = public_base_url(request)
    return JSONResponse({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        # People's clients (Claude, Alexa+ account linking) ask for these; the service scope is only
        # for Alexa+'s machine-to-machine client and would make their sign-in fail.
        "scopes_supported": list(USER_SCOPES),
        "bearer_methods_supported": ["header"],
        "resource_name": "Pantry Pilot",
    })


def authorization_server_metadata(request: Request) -> JSONResponse:
    base = public_base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "scopes_supported": list(ALL_SCOPES),
    })


def _without_resource(pairs: list[tuple[str, str]]) -> str:
    return urllib.parse.urlencode([(k, v) for k, v in pairs if k != "resource"])


def authorize_redirect(config: AuthConfig, request: Request) -> RedirectResponse:
    query = _without_resource(list(request.query_params.multi_items()))
    return RedirectResponse(f"{config.domain}/oauth2/authorize?{query}", status_code=302)


async def token_proxy(config: AuthConfig, request: Request) -> Response:
    form = urllib.parse.parse_qsl((await request.body()).decode(), keep_blank_values=True)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if auth := request.headers.get("authorization"):
        headers["Authorization"] = auth  # client_secret_basic
    upstream = urllib.request.Request(
        f"{config.domain}/oauth2/token", data=_without_resource(form).encode(), headers=headers, method="POST"
    )

    def send() -> tuple[int, bytes, str]:
        try:
            with urllib.request.urlopen(upstream, timeout=10) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers.get("Content-Type", "application/json")

    status, body, content_type = await anyio.to_thread.run_sync(send)
    return Response(body, status_code=status, media_type=content_type, headers={"Cache-Control": "no-store"})
