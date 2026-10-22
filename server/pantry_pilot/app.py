"""HTTP entry point: /mcp for Alexa+ and other MCP clients, /api/* for the Pantry Pilot web app."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import json
import logging
import os
import time
import urllib.parse
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

import anyio
from mcp import Client
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from . import connectors, oauth_app, ordering, voice
from .agent import call_tool_with_events, requested_language, run_agent
from .auth import (
    ALL_SCOPES,
    AccountDirectory,
    AuthConfig,
    BearerAuthMiddleware,
    EitherVerifier,
    TokenVerifier,
    authorization_server_metadata,
    authorize_redirect,
    current_user,
    is_mcp_path,
    is_private_api_path,
    jwks_signing_key,
    protected_resource_metadata,
    public_base_url,
    token_proxy,
)
from .instacart import Instacart, InstacartError, instacart_from_env
from .kroger import CART_URL, Kroger, KrogerError, kroger_from_env
from .mcp_server import CONFIDENT, create_server
from .mealtime import TimezoneMiddleware
from .notify import WHATSAPP_TEMPLATES, Message, _cognito_email, Notifier, NotifyUnavailable, normalise_whatsapp
from .models import GroceryItem, new_id
from .nova import BedrockNova, Nova, NovaUnavailable, nova_from_env
from .store import Store, dumps_for_api, store_from_env
from .whatsapp import READABLE_MEDIA, Inbound, WhatsApp, WhatsAppTranscript, item_lines, parse_event, verify_signature


def _env_list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


class OriginSecretMiddleware:
    """On AWS the Lambda URL is public; only CloudFront knows this header value.
    /health stays open for the Lambda Web Adapter's readiness check."""

    def __init__(self, app: Any, secret: str) -> None:
        self.app = app
        self.secret = secret.encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] != "/health":
            if dict(scope["headers"]).get(b"x-origin-verify") != self.secret:
                await JSONResponse({"error": "forbidden"}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


APP_URL = os.environ.get("APP_URL", "https://trypantrypilot.com")
WHATSAPP_HELLO = {"hi", "hello", "hey", "hii", "menu", "help", "start", "namaste", "hi pantry pilot"}
WHATSAPP_SCAN_WORDS = {"scan", "camera", "photo", "scan groceries", "scan my groceries", "scan bill", "scan my bill"}
WHATSAPP_BUTTON_TEXT = {"meal": "What should I cook now?", "list": "What's on my grocery list?"}

DEMO_EMAIL = "ramu.devhub@gmail.com"
DEMO_UNTIL = 1798761599  # 2026-12-31T23:59:59Z, same as infra/lambda/auth/verify-challenge.mjs

log = logging.getLogger(__name__)
# Lambda shows whatever reaches stderr; uvicorn only sets up its own loggers.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

CHAT_KEEP_SECONDS = 30 * 60
HEARTBEAT_SECONDS = 10
"""A conversation older than this starts fresh."""


def create_app(
    store: Store | None = None,
    nova: Nova | None = None,
    household_id: str | None = None,
    auth: AuthConfig | None = None,
    signing_key: Callable[[str], Any] | None = None,
    connector_client: connectors.ClientFactory = connectors.remote_client,
    account_directory: AccountDirectory | None = None,
    instacart: Instacart | None = None,
    kroger: Kroger | None = None,
    speaker: voice.Speaker | None = None,
    notifier: Notifier | None = None,
    oauth_refresh: oauth_app.Refresh | None = None,
) -> Starlette:
    """`household_id` is the pantry used when nobody is signed in (local development without
    Cognito). With Cognito configured, each signed-in user gets their own pantry."""
    store = store or store_from_env()
    nova = nova or nova_from_env()
    instacart = instacart or instacart_from_env()
    kroger = kroger or kroger_from_env()
    speaker = speaker or voice.Speaker()
    notifier = notifier or Notifier()
    default_household = household_id or os.environ.get("HOUSEHOLD_ID", "demo")
    auth = auth or AuthConfig.from_env()

    def household_id_now() -> str:
        return current_user.get() or default_household

    def demo_account(user: str) -> bool:
        """The account judges sign in to with the fixed code: no real orders, no home address."""
        if time.time() > DEMO_UNTIL:
            return False
        try:
            return (_cognito_email(user) or "").lower() == DEMO_EMAIL
        except Exception:
            return False

    mcp = create_server(store, nova, household_id_now)

    # The conversation is kept in the store, not in memory: Lambda runs several copies of the server
    # and replaces idle ones, so in-memory history made the assistant forget mid-conversation.
    def chat_key() -> str:
        return f"{household_id_now()}#chat"

    def load_chat() -> list[dict[str, Any]]:
        saved = store.load_record(chat_key()) or {}
        return saved.get("messages", []) if time.time() - saved.get("at", 0) < CHAT_KEEP_SECONDS else []

    @mcp.custom_route("/api/config", methods=["GET"])
    async def config(_: Request) -> Response:
        """What the web app needs to sign users in; public, and nothing in it is secret."""
        if not auth or not auth.web_client_id:
            return JSONResponse({"auth": None})
        return JSONResponse({"auth": {
            "region": auth.region,
            "userPoolId": auth.user_pool_id,
            "clientId": auth.web_client_id,
            "emailOtp": auth.email_otp,
        }})

    if auth and auth.email_otp:
        accounts = account_directory or AccountDirectory(auth)

        @mcp.custom_route("/api/auth/start", methods=["POST"])
        async def start_email_sign_in(request: Request) -> Response:
            """Before an email-code sign-in: make sure the account exists. Same answer either way,
            so this doesn't reveal who has an account."""
            email = str((await request.json()).get("email", ""))
            try:
                await anyio.to_thread.run_sync(accounts.ensure_account, email)
            except ValueError as err:
                return JSONResponse({"error": str(err)}, status_code=400)
            return JSONResponse({"ok": True})

    if auth:
        # OAuth for Alexa+ (see auth.py).
        @mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
        async def resource_metadata(request: Request) -> Response:
            return protected_resource_metadata(request)

        @mcp.custom_route("/.well-known/oauth-protected-resource/mcp", methods=["GET"])
        async def resource_metadata_for_mcp(request: Request) -> Response:
            return protected_resource_metadata(request)

        @mcp.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
        async def server_metadata(request: Request) -> Response:
            return authorization_server_metadata(request)

        # Claude (and other first-party MCP clients) sign in on Pantry Pilot's own page, with the
        # emailed code, instead of Cognito's password-only hosted login. See oauth_app.py.
        first_party = {cid: "Claude" for cid in _env_list("COGNITO_CLAUDE_CLIENT_ID")}
        refresh = oauth_refresh or (
            oauth_app.cognito_refresh(auth.region, auth.web_client_id) if auth.web_client_id else None
        )
        web_tokens = TokenVerifier(auth, signing_key or jwks_signing_key(auth), client_ids={auth.web_client_id})

        def oauth_error(err: oauth_app.OAuthError) -> JSONResponse:
            return JSONResponse({"error": err.error, "error_description": err.description}, status_code=err.status)

        def ours(client_id: str) -> bool:
            return bool(refresh) and oauth_app.client(store, client_id, first_party) is not None

        @mcp.custom_route("/oauth/register", methods=["POST"])
        async def register(request: Request) -> Response:
            """Dynamic Client Registration: MCP clients (Claude) connect with just our URL."""
            if not refresh:
                return JSONResponse({"error": "invalid_request", "error_description": "Registration is off."}, status_code=400)
            try:
                body = await request.json()
                registered = await anyio.to_thread.run_sync(oauth_app.register, store, body if isinstance(body, dict) else {})
            except ValueError:
                return JSONResponse({"error": "invalid_client_metadata", "error_description": "Send JSON."}, status_code=400)
            except oauth_app.OAuthError as err:
                return oauth_error(err)
            return JSONResponse(registered, status_code=201, headers={"Cache-Control": "no-store"})

        @mcp.custom_route("/oauth/authorize", methods=["GET"])
        async def authorize(request: Request) -> Response:
            params = dict(request.query_params)
            if not ours(params.get("client_id", "")):
                return authorize_redirect(auth, request)
            try:
                request_id = await anyio.to_thread.run_sync(oauth_app.start, store, params, first_party)
            except oauth_app.OAuthError as err:
                return oauth_error(err)
            return RedirectResponse(f"/?oauth={request_id}", status_code=302)

        @mcp.custom_route("/oauth/token", methods=["POST"])
        async def token(request: Request) -> Response:
            form = dict(urllib.parse.parse_qsl((await request.body()).decode(), keep_blank_values=True))
            client_id, client_secret = form.get("client_id", ""), form.get("client_secret")
            if (basic := request.headers.get("authorization", "")).lower().startswith("basic "):
                with contextlib.suppress(ValueError, UnicodeDecodeError):
                    user, _, password = base64.b64decode(basic.split()[-1]).decode().partition(":")
                    client_id = urllib.parse.unquote_plus(user) or client_id
                    client_secret = urllib.parse.unquote_plus(password) or client_secret
            if not ours(client_id):
                return await token_proxy(auth, request)
            try:
                tokens = await anyio.to_thread.run_sync(oauth_app.token, store, form, client_id, refresh, client_secret)
            except oauth_app.OAuthError as err:
                return oauth_error(err)
            return JSONResponse(tokens, headers={"Cache-Control": "no-store"})

        @mcp.custom_route("/api/oauth/requests/{request_id}", methods=["GET"])
        async def oauth_request(request: Request) -> Response:
            try:
                pending = oauth_app.pending(store, request.path_params["request_id"])
            except oauth_app.OAuthError as err:
                return oauth_error(err)
            return JSONResponse({
                "client": pending["client_name"],
                "verified": pending.get("verified", True),
                "redirectHost": urllib.parse.urlparse(pending["redirect_uri"]).netloc,
            })

        @mcp.custom_route("/api/oauth/requests/{request_id}/approve", methods=["POST"])
        async def oauth_approve(request: Request) -> Response:
            body = await request.json()
            subject = lambda access: web_tokens.verify(access)["sub"]  # noqa: E731
            try:
                url = await anyio.to_thread.run_sync(
                    oauth_app.approve, store, request.path_params["request_id"], household_id_now(),
                    str(body.get("refreshToken", "")), refresh, subject,
                )
            except oauth_app.OAuthError as err:
                return oauth_error(err)
            return JSONResponse({"redirect": url})

        @mcp.custom_route("/api/oauth/requests/{request_id}/deny", methods=["POST"])
        async def oauth_deny(request: Request) -> Response:
            try:
                url = await anyio.to_thread.run_sync(oauth_app.deny, store, request.path_params["request_id"])
            except oauth_app.OAuthError as err:
                return oauth_error(err)
            return JSONResponse({"redirect": url})

    def state_response() -> Response:
        hid = household_id_now()
        household = store.load(hid)
        # Drop timers that finished more than a minute ago; the web app announces them.
        cutoff = int(time.time() * 1000) - 60_000
        if any(t.ends_at < cutoff for t in household.timers):
            household.timers = [t for t in household.timers if t.ends_at >= cutoff]
            store.save(hid, household)
        return Response(dumps_for_api(household), media_type="application/json")

    @mcp.custom_route("/api/state", methods=["GET"])
    async def get_state(_: Request) -> Response:
        hid = household_id_now()
        number = store.load(hid).notifications.whatsapp
        # Numbers saved before WhatsApp chat existed get linked the next time the app opens.
        if number and not store.load_record(f"whatsapp#{number}"):
            store.save_record(f"whatsapp#{number}", {"household": hid})
        return state_response()

    @mcp.custom_route("/api/voice/stream", methods=["GET"])
    async def voice_stream(request: Request) -> Response:
        """A signed Amazon Transcribe streaming URL for hands-free "Alexa, …" in any browser."""
        try:
            url = await anyio.to_thread.run_sync(voice.stream_url, request.query_params.get("lang", "en-US"))
        except voice.VoiceUnavailable as err:
            return JSONResponse({"error": str(err)}, status_code=503)
        return JSONResponse({"url": url, "sampleRate": voice.SAMPLE_RATE})

    @mcp.custom_route("/api/voice/speak", methods=["POST"])
    async def voice_speak(request: Request) -> Response:
        """A reply spoken by Amazon Polly, as MP3. Phones play this far more reliably than the
        browser's own speech."""
        try:
            body = await request.json()
            text, language = str(body.get("text", "")), str(body.get("lang", "en-US"))
        except (ValueError, AttributeError):
            return JSONResponse({"error": "Send JSON with text."}, status_code=400)
        try:
            audio = await anyio.to_thread.run_sync(speaker.speak, text, language)
        except ValueError as err:
            return JSONResponse({"error": str(err)}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Speech is unavailable right now."}, status_code=503)
        return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})

    @mcp.custom_route("/api/turn", methods=["POST"])
    async def turn(request: Request) -> Response:
        """One assistant turn, streamed as server-sent events for the tool trace.
        Body: {text} for chat, {images|video|document, kind} to scan, or {confirmItems} to save a scan."""
        body = await request.json()

        async def produce(queue: asyncio.Queue[str | None]) -> None:
            try:
                async with Client(mcp) as client:
                    async for event in _turn_events(body, client):
                        await queue.put(f"data: {json.dumps(event)}\n\n")
            finally:
                await queue.put(None)

        async def events() -> AsyncIterator[str]:
            # Set before the MCP client starts, so its tool calls (place_order) see this turn's words.
            turn = ordering.current_turn.set({"id": new_id(), "text": str(body.get("text", "")).strip()})
            queue: asyncio.Queue[str | None] = asyncio.Queue()
            task = asyncio.create_task(produce(queue))
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                    except TimeoutError:
                        # A long step (Nova inventing dishes, a shopping app) must not leave the
                        # connection silent, or CloudFront gives up and the app shows "offline".
                        yield ": still working\n\n"
                        continue
                    if chunk is None:
                        break
                    yield chunk
                await task  # re-raise anything the turn failed with
            finally:
                task.cancel()
                ordering.current_turn.reset(turn)
            household = json.loads(dumps_for_api(store.load(household_id_now())))
            yield f"data: {json.dumps({'type': 'state', 'state': household})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def _turn_events(body: dict[str, Any], client: Client) -> AsyncIterator[dict[str, Any]]:
        media_args = {k: body[k] for k in ("images", "video", "document") if body.get(k)}
        if media_args:
            # Show sizes rather than megabytes of base64 in the tool trace.
            display = {k: f"{len(v)} photos" if k == "images" else "attached" for k, v in media_args.items()}
            display["kind"] = body.get("kind", "auto")
            payload: dict[str, Any] = {}
            ok = True
            async for event in call_tool_with_events(client, "scan_pantry", {**media_args, "kind": display["kind"]}, display):
                if event["type"] == "tool_end":
                    payload, ok = event["result"], event["ok"]
                yield event
            if not ok:
                yield {"type": "reply", "text": f"Sorry, I couldn't read that. {payload.get('summary', '')}".strip()}
                return
            items = payload.get("items", [])
            unsure = sum(1 for i in items if i["confidence"] < CONFIDENT)
            check = f", and {unsure} {'need' if unsure > 1 else 'needs'} a quick check" if unsure else ""
            yield {"type": "detections", "items": items}
            yield {"type": "reply", "text": f"I found {len(items)} items{check}. Fix any quantities, then tap Add to pantry."}
            return

        if confirmed := body.get("confirmItems"):
            args = {
                "action": "add",
                "source": "scan",
                "items": [
                    {k: i.get(k) for k in ("name", "quantity", "unit", "emoji", "category")}
                    | {"expires_in_days": i.get("expiresInDays", i.get("expires_in_days"))}
                    for i in confirmed
                ],
            }
            async for event in call_tool_with_events(client, "update_inventory", args):
                yield event
            count = f"{len(confirmed)} item" + ("s" if len(confirmed) != 1 else "")
            yield {"type": "reply", "text": f"Done, I added {count} to your pantry. Want a meal idea that uses them?"}
            return

        text = str(body.get("text", "")).strip()
        if not text:
            yield {"type": "reply", "text": "I didn't catch that. Could you say it again?"}
            return
        try:
            history = await anyio.to_thread.run_sync(load_chat)
            before = len(history)
            try:
                # "Talk to me in Bengali" is saved before the agent runs, so the reply (and any meal
                # question in the same sentence) already uses the new language.
                if language := requested_language(text):
                    async for event in call_tool_with_events(client, "set_food_preferences", {"language": language}):
                        yield event
                prefs = store.load(household_id_now()).preferences
                async for event in run_agent(text, history, client, nova, language=prefs.language, region=prefs.region):
                    yield event
            finally:
                if len(history) != before:
                    record = {"messages": history, "at": time.time()}
                    await anyio.to_thread.run_sync(store.save_record, chat_key(), record)
        except NovaUnavailable as err:
            yield {"type": "reply", "text": str(err)}

    @mcp.custom_route("/api/pantry", methods=["POST"])
    async def add_to_pantry(request: Request) -> Response:
        """Manual entry from the dashboard: {items: [{name, quantity, unit, category?, emoji?, expiresInDays?}]}.
        Goes through the same update_inventory MCP tool the agent and Alexa+ use."""
        items = (await request.json()).get("items") or []
        args = {
            "action": "add",
            "source": "manual",
            "items": [
                {k: i.get(k) for k in ("name", "quantity", "unit", "category", "emoji")}
                | {"expires_in_days": i.get("expiresInDays")}
                for i in items
                if str(i.get("name", "")).strip()
            ],
        }
        async with Client(mcp) as client:
            result = await client.call_tool("update_inventory", args)
        if result.is_error:
            return JSONResponse({"error": "Couldn't add that item"}, status_code=400)
        return state_response()

    @mcp.custom_route("/api/plan", methods=["POST"])
    async def plan(request: Request) -> Response:
        """A fresh meal plan from the dashboard: {meal?, request?}. Same MCP tool as voice."""
        body = await request.json() if await request.body() else {}
        args = {k: body[k] for k in ("meal", "request") if body.get(k)}
        async with Client(mcp) as client:
            result = await client.call_tool("plan_meals", args)
        if result.is_error:
            return JSONResponse({"error": "Couldn't plan meals right now"}, status_code=400)
        return state_response()

    @mcp.custom_route("/api/notify/test", methods=["POST"])
    async def notify_test(_: Request) -> Response:
        """Send the signed-in user a sample order email, to check notifications reach them."""
        message = Message(
            subject="Order updates from Pantry Pilot",
            heading="Order updates will arrive here",
            intro="When Alexa places an order or needs you to pay, the summary and payment link come to this inbox.",
            items=[{"name": "Milk 1 L", "quantity": "2", "price": "₹68"}, {"name": "Onions 1 kg", "price": "₹42"}],
            total="₹110 (sample)",
            footer="This is a test. No order was placed.",
        )
        try:
            address = await anyio.to_thread.run_sync(notifier.email, household_id_now(), message)
        except NotifyUnavailable as err:
            return JSONResponse({"error": str(err)}, status_code=503)
        return JSONResponse({"sentTo": address})

    @mcp.custom_route("/api/notify/whatsapp", methods=["POST"])
    async def set_whatsapp(request: Request) -> Response:
        """{number}: send order updates to this WhatsApp number too (a confirmation goes there
        first); an empty number turns WhatsApp updates off."""
        raw = str((await request.json()).get("number", "")).strip()
        hid = household_id_now()
        try:
            number = normalise_whatsapp(raw) if raw else ""
        except ValueError as err:
            return JSONResponse({"error": str(err)}, status_code=400)
        if number:
            try:
                await anyio.to_thread.run_sync(notifier.whatsapp, number, WHATSAPP_TEMPLATES["updates_on"], ["there"])
            except NotifyUnavailable as err:
                return JSONResponse({"error": str(err)}, status_code=503)
        household = store.load(hid)
        if household.notifications.whatsapp and household.notifications.whatsapp != number:
            store.delete_record(f"whatsapp#{household.notifications.whatsapp}")
        household.notifications.whatsapp = number
        store.save(hid, household)
        if number:
            # Messages from this number to Pantry Pilot's WhatsApp act on this household.
            store.save_record(f"whatsapp#{number}", {"household": hid})
        return state_response()

    @mcp.custom_route("/api/preferences", methods=["POST"])
    async def set_preferences(request: Request) -> Response:
        """Food preferences from the dashboard: {diet?, cuisines?, avoid?}. Same MCP tool as voice."""
        body = await request.json()
        args = {k: body[k] for k in ("diet", "cuisines", "avoid", "location") if k in body}
        if "homeCuisine" in body:
            args["home_cuisine"] = body["homeCuisine"]
        if "language" in body:
            args["language"] = body["language"]
        if "region" in body:
            args["region"] = body["region"] or "auto"
        async with Client(mcp) as client:
            result = await client.call_tool("set_food_preferences", args)
        if result.is_error:
            return JSONResponse({"error": "Couldn't save those preferences"}, status_code=400)
        return state_response()

    # ---------- Grocery-app connectors (Swiggy Instamart, Zepto); see connectors.py ----------

    async def import_orders(provider_id: str) -> dict[str, Any]:
        """Sync one app's new delivered orders into the pantry through update_inventory."""
        provider = connectors.PROVIDERS[provider_id]
        result = await connectors.sync_orders(store, household_id_now(), provider, nova, connector_client)
        if result.items:
            args = {
                "action": "add",
                "source": "order",
                "items": [
                    {"name": i.name, "quantity": i.quantity, "unit": i.unit, "emoji": i.emoji,
                     "category": i.category, "expires_in_days": i.expires_in_days}
                    for i in result.items
                ],
            }
            async with Client(mcp) as client:
                await client.call_tool("update_inventory", args)
        orders, items = len(result.new_orders), len(result.items)
        summary = (
            f"Imported {items} item{'s' if items != 1 else ''} from {orders} new {provider.name} order{'s' if orders != 1 else ''}"
            if orders else f"No new delivered {provider.name} orders"
        )
        return {"summary": summary, "orders": result.new_orders, "items": [i.name for i in result.items]}

    # ---------- Price comparison and confirmed ordering (see ordering.py for the rules) ----------

    def connected_shops() -> list[connectors.Provider]:
        user = household_id_now()
        return [
            p for p in connectors.PROVIDERS.values()
            if p.id in ordering.SHOP_TOOLS and store.load_record(f"{user}#connector#{p.id}")
        ]

    def shopping_items(items: list[str] | None) -> list[str]:
        wanted = [i.strip() for i in items or [] if i.strip()]
        if not wanted:
            wanted = [g.name for g in store.load(household_id_now()).grocery if not g.checked]
        if not wanted:
            raise ordering.OrderError("Your grocery list is empty. Tell me what to buy.")
        return wanted[:12]

    @mcp.tool(title="Compare grocery prices")
    async def compare_grocery_prices(items: list[str] | None = None) -> dict[str, Any]:
        """India: compare prices for these items (default: the open grocery list) on every connected
        app (Swiggy Instamart, Zepto) at the user's saved address. Read the cheapest app and its
        total back; offer to prepare the order there. Never orders anything."""
        user = household_id_now()
        try:
            wanted = shopping_items(items)
            shops = connected_shops()
            if not shops:
                raise ordering.OrderError("Connect Zepto or Swiggy Instamart first, under Shopping apps.")
            quotes: list[ordering.Quote] = []
            errors: dict[str, str] = {}

            async def one(p: connectors.Provider) -> None:
                try:
                    async with ordering.app_session(store, user, p, connector_client) as s:
                        quotes.append(await ordering.quote(s, nova, wanted))
                except (ordering.OrderError, connectors.ConnectorError, NovaUnavailable) as err:
                    errors[p.name] = str(err)

            async with anyio.create_task_group() as tg:
                for p in shops:
                    tg.start_soon(one, p)
        except ordering.OrderError as err:
            return {"summary": str(err), "apps": []}
        await anyio.to_thread.run_sync(ordering.save_comparison, store, user, quotes)
        complete = sorted((q for q in quotes if not q.missing), key=lambda q: q.total)
        rows = [
            {
                "app": connectors.PROVIDERS[q.app].name,
                "total": q.total,
                "items": [{"item": p.item, "product": f"{p.name} {p.pack}".strip(), "price": p.price} for p in q.picks],
                "missing": q.missing,
            }
            for q in sorted(quotes, key=lambda q: (bool(q.missing), q.total))
        ]
        if complete:
            best = complete[0]
            summary = f"Cheapest: {connectors.PROVIDERS[best.app].name} at {ordering.rupees(best.total)} for {len(wanted)} items (before fees)"
        elif quotes:
            summary = "No app has everything; see what's missing per app"
        else:
            summary = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "No prices found"
        return {"summary": summary, "apps": rows, "errors": errors}

    @mcp.tool(title="Prepare an order")
    async def prepare_order(app: Literal["zepto", "swiggy"], items: list[str] | None = None) -> dict[str, Any]:
        """India: fill the user's Zepto or Swiggy Instamart cart with these items (default: the open
        grocery list) and get the bill. Does NOT order. Read the returned `readback` to the user
        word for word; they must then say "place the order" for place_order to work."""
        user = household_id_now()
        provider = connectors.PROVIDERS[app]
        try:
            wanted = shopping_items(items)
            async with ordering.app_session(store, user, provider, connector_client) as s:
                q = ordering.saved_quote(store, user, app, wanted)
                if q:
                    await s.call("select_saved_address", {"addressId": q.address_id}) if app == "zepto" else None
                else:
                    q = await ordering.quote(s, nova, wanted)
                if not q.picks:
                    raise ordering.OrderError(f"I couldn't find those items on {provider.name}.")
                bill = await ordering._fill_and_bill(s, nova, q, user)
        except (ordering.OrderError, connectors.ConnectorError, NovaUnavailable) as err:
            return {"summary": str(err), "readback": str(err)}
        draft = ordering.save_draft(store, user, q, bill)
        text = ordering.readback(q, bill, draft["payment"], provider)
        if q.address and demo_account(user):
            text = text.replace(q.address, "your saved address")
        return {"summary": f"{provider.name} cart ready · {ordering.rupees(bill['total'])}", "readback": text}

    @mcp.tool(title="Place the order")
    async def place_order() -> dict[str, Any]:
        """India: place the order prepared by prepare_order. Only works in a later turn where the user
        said "place the order" themselves; never call it on "yes", "ok" or your own initiative.
        Repeat the returned summary to the user."""
        user = household_id_now()
        if demo_account(user):
            ordering.clear_draft(store, user)
            return {"summary": "This is the demo account, so I won't place real orders. Everything up to the order works.", "placed": False}
        try:
            draft = ordering.confirmed_draft(store, user)
            provider = connectors.PROVIDERS[draft["app"]]
            async with ordering.app_session(store, user, provider, connector_client) as s:
                result = await ordering.place(s, nova, draft, user)
        except (ordering.OrderError, connectors.ConnectorError, NovaUnavailable) as err:
            return {"summary": str(err), "placed": False}
        ordering.clear_draft(store, user)
        if not result["placed"]:
            return {"summary": result["message"] or f"{provider.name} didn't place the order.", "placed": False}
        await notify_order(provider, draft, result)
        how = "cash on delivery" if draft["payment"] == "cod" else "I've sent you the payment link"
        return {
            "summary": f"{provider.name} order placed, {ordering.rupees(result['total'])}, {how}.",
            "placed": True,
            "orderId": result["order_id"],
            "message": result["message"],
        }

    async def notify_order(provider: connectors.Provider, draft: dict[str, Any], result: dict[str, Any]) -> None:
        """Email (and WhatsApp, when set up) the order summary or payment link; never blocks the order."""
        user = household_id_now()
        picks = draft["quote"]["picks"]
        items = [{"name": f"{p['name']} {p['pack']}".strip(), "quantity": str(p["quantity"]), "price": ordering.rupees(p["price"])} for p in picks]
        total = ordering.rupees(result["total"])
        link = result["payment_url"]
        message = Message(
            subject=f"{provider.name} order {'placed' if not link else 'waiting for payment'} · {total}",
            heading=f"Your {provider.name} order is {'placed' if not link else 'waiting for payment'}",
            intro=f"Pantry Pilot placed it after you said “place the order”. Delivery to {draft['quote']['address']}.",
            items=items,
            total=f"{total} · {'cash on delivery' if not link else 'pay online'}",
            button={"label": f"Pay {total} on {provider.name}", "url": link} if link else None,
            footer=f"Order {result['order_id']}. To cancel, use the {provider.name} app." if result["order_id"] else f"To cancel, use the {provider.name} app.",
        )
        try:
            await anyio.to_thread.run_sync(notifier.email, user, message)
        except Exception:
            log.warning("Order email failed", exc_info=True)
        number = store.load(user).notifications.whatsapp
        if number:
            names = ", ".join(i["name"] for i in items)[:200]
            template, params = (
                (WHATSAPP_TEMPLATES["payment_link"], [provider.name, total, link])
                if link
                else (WHATSAPP_TEMPLATES["order_placed"], [provider.name, names, total, "cash on delivery"])
            )
            try:
                await anyio.to_thread.run_sync(notifier.whatsapp, number, template, params)
            except Exception:
                log.warning("Order WhatsApp failed", exc_info=True)

    @mcp.tool(title="Sync grocery orders")
    async def sync_grocery_orders(app: Literal["zepto", "swiggy"]) -> dict[str, Any]:
        """Import the user's newly delivered Zepto or Swiggy Instamart orders into the pantry.
        Only reads order history; never places, changes or pays for orders."""
        try:
            return await import_orders(app)
        except connectors.ConnectorError as err:
            return {"summary": str(err), "orders": [], "items": []}

    def connector_or_404(request: Request) -> connectors.Provider | None:
        return connectors.PROVIDERS.get(request.path_params["provider"])

    async def connector_action(request: Request, action: Callable[[connectors.Provider], Any]) -> Response:
        provider = connector_or_404(request)
        if not provider:
            return JSONResponse({"error": "Unknown app"}, status_code=404)
        try:
            extra = await action(provider)
        except connectors.ConnectorError as err:
            return JSONResponse({"error": str(err)}, status_code=400)
        return JSONResponse({"connectors": connectors.status(store, household_id_now(), public_base_url(request)), **(extra or {})})

    @mcp.custom_route("/api/connectors", methods=["GET"])
    async def list_connectors(request: Request) -> Response:
        return JSONResponse({"connectors": connectors.status(store, household_id_now(), public_base_url(request))})

    @mcp.custom_route("/api/connectors/{provider}/start", methods=["POST"])
    async def start_connector(request: Request) -> Response:
        user = household_id_now()

        app_url = public_base_url(request)

        async def start(p: connectors.Provider) -> dict[str, Any]:
            url = await anyio.to_thread.run_sync(connectors.start_sign_in, store, user, p, app_url)
            return {"authUrl": url, "directReturn": connectors.redirect_uri_for(p, app_url) != connectors.REDIRECT_URI}

        return await connector_action(request, start)

    async def record_tools(user: str, provider: connectors.Provider) -> None:
        """Note the app's tool names after connecting (for ordering); never blocks the sign-in."""
        try:
            await connectors.tool_catalog(store, user, provider, connector_client)
        except Exception:
            log.warning("Couldn't list %s tools", provider.name, exc_info=True)

    @mcp.custom_route("/api/connectors/{provider}/callback", methods=["GET"])
    async def connector_callback(request: Request) -> Response:
        """Where Swiggy (and any app that allows our address) returns the browser after sign-in.
        Public: the one-time state from start_sign_in says whose account to connect."""
        provider = connector_or_404(request)
        if not provider:
            return RedirectResponse("/?connectError=" + urllib.parse.quote("Unknown app"), status_code=303)
        if error := request.query_params.get("error"):
            reason = request.query_params.get("error_description") or error
            message = f"{provider.name} sign-in was cancelled ({reason})."
            return RedirectResponse("/?connectError=" + urllib.parse.quote(message), status_code=303)
        try:
            user = await anyio.to_thread.run_sync(connectors.finish_from_redirect, store, provider, str(request.url))
        except connectors.ConnectorError as err:
            return RedirectResponse("/?connectError=" + urllib.parse.quote(str(err)), status_code=303)
        await record_tools(user, provider)
        return RedirectResponse(f"/?connected={provider.id}", status_code=303)

    @mcp.custom_route("/api/connectors/{provider}/finish", methods=["POST"])
    async def finish_connector(request: Request) -> Response:
        user, url = household_id_now(), str((await request.json()).get("redirectUrl", ""))

        async def finish(p: connectors.Provider) -> None:
            await anyio.to_thread.run_sync(connectors.finish_sign_in, store, user, p, url)
            await record_tools(user, p)

        return await connector_action(request, finish)

    @mcp.custom_route("/api/connectors/{provider}/sync", methods=["POST"])
    async def sync_connector(request: Request) -> Response:
        async def sync(p: connectors.Provider) -> dict[str, Any]:
            result = await import_orders(p.id)
            await record_tools(household_id_now(), p)
            return {"sync": result, "state": json.loads(dumps_for_api(store.load(household_id_now())))}

        return await connector_action(request, sync)

    @mcp.custom_route("/api/connectors/{provider}/disconnect", methods=["POST"])
    async def disconnect_connector(request: Request) -> Response:
        user = household_id_now()

        async def disconnect(p: connectors.Provider) -> None:
            connectors.disconnect(store, user, p)

        return await connector_action(request, disconnect)

    # ---------- Instacart (US): send the grocery list to an Instacart shopping-list page ----------

    async def instacart_link(linkback: str | None) -> dict[str, Any]:
        hid = household_id_now()
        names = [g.name for g in store.load(hid).grocery if not g.checked]
        url = await anyio.to_thread.run_sync(instacart.shopping_list_url, store, hid, names, linkback)
        noun = "item" if len(names) == 1 else "items"
        return {"url": url, "items": names, "summary": f"Your Instacart list with {len(names)} {noun} is ready."}

    @mcp.tool(title="Shop on Instacart")
    async def shop_on_instacart() -> dict[str, Any]:
        """US only: put the open grocery list on an Instacart page where the user picks a store and
        checks out themselves. Pantry Pilot never places or pays for the order. Tell the user the
        link is waiting in the Pantry Pilot app; don't read the URL aloud."""
        try:
            return await instacart_link(None)
        except InstacartError as err:
            return {"url": None, "items": [], "summary": str(err)}

    @mcp.custom_route("/api/instacart", methods=["GET"])
    async def instacart_status(_: Request) -> Response:
        enabled = await anyio.to_thread.run_sync(lambda: instacart.enabled)
        return JSONResponse({"enabled": enabled})

    @mcp.custom_route("/api/instacart", methods=["POST"])
    async def instacart_shop(request: Request) -> Response:
        try:
            return JSONResponse(await instacart_link(public_base_url(request)))
        except InstacartError as err:
            return JSONResponse({"error": str(err)}, status_code=400)

    # ---------- Kroger (US): match the grocery list, add the products the user picks to their cart ----------

    async def kroger_matches(names: list[str]) -> list[dict[str, Any]]:
        results: dict[str, list[dict[str, Any]]] = {}

        async def one(name: str) -> None:
            results[name] = await anyio.to_thread.run_sync(kroger.search, name)

        async with anyio.create_task_group() as tg:
            for name in names:
                tg.start_soon(one, name)
        return [{"item": n, "products": results.get(n, [])} for n in names]

    def kroger_fail(err: Exception) -> Response:
        if isinstance(err, BaseExceptionGroup):
            err = next((e for e in err.exceptions if isinstance(e, KrogerError)), err)
        return JSONResponse({"error": str(err) if isinstance(err, KrogerError) else "Kroger is unavailable."}, status_code=400)

    @mcp.tool(title="Find Kroger products")
    async def find_kroger_products(items: list[str]) -> dict[str, Any]:
        """US only. Look up grocery items in Kroger's catalog. Read the top match for each item back to
        the user (description and size, exactly as given) and ask before adding anything; then call
        add_to_kroger_cart with the UPCs they agreed to. Never compare these with other stores' prices."""
        try:
            matches = await kroger_matches([i for i in items if i.strip()][:10])
        except (KrogerError, BaseExceptionGroup) as err:
            return {"matches": [], "summary": str(err) if isinstance(err, KrogerError) else "Kroger is unavailable."}
        found = [m for m in matches if m["products"]]
        return {"matches": matches, "summary": f"Found {len(found)} of {len(matches)} items at Kroger."}

    @mcp.tool(title="Add to Kroger cart")
    async def add_to_kroger_cart(upcs: list[str], modality: Literal["PICKUP", "DELIVERY"] = "PICKUP") -> dict[str, Any]:
        """US only. Add products to the user's own Kroger cart. Only call this with UPCs from
        find_kroger_products that the user has just explicitly said yes to. The user checks out on
        kroger.com themselves; this never places or pays for an order."""
        try:
            count = await anyio.to_thread.run_sync(
                kroger.add_to_cart, store, household_id_now(), [{"upc": u, "quantity": 1} for u in upcs], modality
            )
        except KrogerError as err:
            return {"added": 0, "summary": str(err)}
        noun = "item" if count == 1 else "items"
        return {"added": count, "summary": f"Added {count} {noun} to your Kroger cart. Check out on kroger.com."}

    @mcp.custom_route("/api/kroger", methods=["GET"])
    async def kroger_status(_: Request) -> Response:
        enabled = await anyio.to_thread.run_sync(lambda: kroger.enabled)
        return JSONResponse({"enabled": enabled, "connected": enabled and kroger.connected(store, household_id_now())})

    @mcp.custom_route("/api/kroger/start", methods=["POST"])
    async def kroger_start(request: Request) -> Response:
        base = public_base_url(request)
        origin = request.headers.get("origin", "")
        # Come back to the page that asked: the app itself, or the Vite dev server locally.
        return_to = origin if origin == base or origin.startswith(("http://localhost:", "http://127.0.0.1:")) else ""
        try:
            url = await anyio.to_thread.run_sync(
                kroger.start_sign_in, store, household_id_now(), f"{base}/api/kroger/callback", return_to
            )
        except KrogerError as err:
            return kroger_fail(err)
        return JSONResponse({"authUrl": url})

    @mcp.custom_route("/api/kroger/callback", methods=["GET"])
    async def kroger_callback(request: Request) -> Response:
        """Public: Kroger returns the browser here; the one-time state says whose account it is."""
        params = request.query_params
        if error := params.get("error"):
            reason = params.get("error_description") or error
            return RedirectResponse("/?connectError=" + urllib.parse.quote(f"Kroger sign-in was cancelled ({reason})."), status_code=303)
        try:
            return_to = await anyio.to_thread.run_sync(kroger.finish_sign_in, store, params.get("state", ""), params.get("code", ""))
        except KrogerError as err:
            return RedirectResponse("/?connectError=" + urllib.parse.quote(str(err)), status_code=303)
        return RedirectResponse(f"{return_to}/?connected=kroger", status_code=303)

    @mcp.custom_route("/api/kroger/match", methods=["POST"])
    async def kroger_match(_: Request) -> Response:
        names = [g.name for g in store.load(household_id_now()).grocery if not g.checked][:20]
        if not names:
            return JSONResponse({"error": "Your grocery list is empty. Add a few items first."}, status_code=400)
        try:
            return JSONResponse({"matches": await kroger_matches(names)})
        except (KrogerError, BaseExceptionGroup) as err:
            return kroger_fail(err)

    @mcp.custom_route("/api/kroger/cart", methods=["POST"])
    async def kroger_cart(request: Request) -> Response:
        """Adds exactly the products the user ticked in the review step."""
        body = await request.json()
        modality = "DELIVERY" if body.get("modality") == "DELIVERY" else "PICKUP"
        try:
            count = await anyio.to_thread.run_sync(kroger.add_to_cart, store, household_id_now(), body.get("items", []), modality)
        except KrogerError as err:
            return kroger_fail(err)
        return JSONResponse({"added": count, "cartUrl": CART_URL})

    @mcp.custom_route("/api/kroger/disconnect", methods=["POST"])
    async def kroger_disconnect(_: Request) -> Response:
        kroger.disconnect(store, household_id_now())
        return JSONResponse({"enabled": True, "connected": False})

    @mcp.custom_route("/api/grocery", methods=["POST"])
    async def grocery(request: Request) -> Response:
        """Direct list edits from the web app: {add: name} | {toggle: id} | {clearChecked: true}."""
        body = await request.json()
        hid = household_id_now()
        household = store.load(hid)
        if name := str(body.get("add", "")).strip():
            household.grocery.append(GroceryItem(name=name.capitalize(), source="manual"))
        elif toggle := body.get("toggle"):
            for g in household.grocery:
                if g.id == toggle:
                    g.checked = not g.checked
        elif body.get("clearChecked"):
            household.grocery = [g for g in household.grocery if not g.checked]
        store.save(hid, household)
        return state_response()

    @mcp.custom_route("/api/timers/cancel", methods=["POST"])
    async def cancel_timer(request: Request) -> Response:
        timer_id = (await request.json()).get("id")
        hid = household_id_now()
        household = store.load(hid)
        household.timers = [t for t in household.timers if t.id != timer_id]
        store.save(hid, household)
        return state_response()

    async def whatsapp_reply(m: Inbound, wa: Any) -> None:
        """Answer one WhatsApp message. `wa` sends the replies: the real WhatsApp number, or the
        in-app preview's transcript (same code, so the preview shows exactly what WhatsApp would)."""
        to = m.sender
        send = lambda fn, *args: anyio.to_thread.run_sync(fn, to, *args)  # noqa: E731
        scan_key = f"{household_id_now()}#whatsapp-scan"
        words = m.text.lower().strip(" !.?")

        if m.kind == "audio":
            # A voice note: hear it with Amazon Transcribe, then answer it like a typed message.
            try:
                audio = await anyio.to_thread.run_sync(wa.download, m.media_id)
                heard = await anyio.to_thread.run_sync(voice.transcribe_note, audio, m.mime, getattr(wa, "media_bucket", ""))
            except Exception:  # noqa: BLE001 - download or Transcribe failed; say so instead of going quiet
                log.warning("WhatsApp voice note failed", exc_info=True)
                heard = None
            if not heard:
                await send(wa.text, "Sorry, I couldn't hear that clearly. Please try again, or type your question." if heard == "" else "Sorry, I couldn't play that voice note. Please type your question instead.")
                return
            await send(wa.text, f"🎤 “{heard}”")
            m = dataclasses.replace(m, kind="text", text=heard)
            words = heard.lower().strip(" !.?")

        if m.kind in ("image", "document"):
            if m.mime not in READABLE_MEDIA:
                await send(wa.text, "I can read photos (JPEG or PNG) and PDF bills. Please send one of those.")
                return
            await send(wa.text, "Got it, reading your " + ("bill" if m.kind == "document" else "photo") + " with Amazon Nova…")
            data = await anyio.to_thread.run_sync(wa.download, m.media_id)
            url = f"data:{m.mime};base64,{base64.b64encode(data).decode()}"
            kind = "receipt" if m.kind == "document" or any(w in words for w in ("bill", "receipt", "invoice")) else "auto"
            args = {"document": url} if m.kind == "document" else {"images": [url]}
            async with Client(mcp) as client:
                result = await client.call_tool("scan_pantry", {**args, "kind": kind})
            items = (result.structured_content or {}).get("items", []) if not result.is_error else []
            if not items:
                await send(wa.text, "I couldn't spot any groceries in that. Try a closer, brighter photo, or a clear photo of the bill.")
                return
            await anyio.to_thread.run_sync(store.save_record, scan_key, {"items": items, "at": time.time()})
            await send(
                wa.buttons,
                f"I found {len(items)} item{'s' if len(items) != 1 else ''}:\n\n{item_lines(items)}\n\nAdd them to your pantry?",
                [("scan_add", "Add to pantry"), ("scan_cancel", "Cancel")],
            )
            return

        if m.kind == "button" and m.button in ("scan_add", "scan_cancel"):
            pending = await anyio.to_thread.run_sync(store.load_record, scan_key)
            await anyio.to_thread.run_sync(store.delete_record, scan_key)
            if m.button == "scan_cancel" or not pending:
                await send(wa.text, "Okay, nothing added." if pending or m.button == "scan_cancel" else "That scan has expired. Send the photo again.")
                return
            async with Client(mcp) as client:
                async for _ in _turn_events({"confirmItems": pending["items"]}, client):
                    pass
            count = len(pending["items"])
            await send(
                wa.buttons,
                f"Done, I added {count} item{'s' if count != 1 else ''} to your pantry. Want a meal idea that uses them?",
                [("meal", "Meal idea"), ("list", "Grocery list")],
            )
            return

        if (m.kind == "button" and m.button == "scan") or words in WHATSAPP_SCAN_WORDS:
            await send(
                wa.link,
                "Send me a photo of your groceries, your fridge or the bill right here, and I'll add the items "
                "to your pantry. Or open the camera in Pantry Pilot:",
                "Open the camera",
                f"{APP_URL}/?scan=1",
            )
            return

        if m.kind == "text" and words in WHATSAPP_HELLO:
            await send(
                wa.buttons,
                "Hi, I'm Pantry Pilot 👋 Ask me anything about your kitchen: what to cook, what's running out, "
                "your grocery list, or prices on Zepto and Swiggy. Send a photo of your groceries or a bill "
                "and I'll add them to your pantry.",
                [("meal", "What to cook"), ("list", "Grocery list"), ("scan", "Scan groceries")],
            )
            return

        text = WHATSAPP_BUTTON_TEXT.get(m.button, "") if m.kind == "button" else m.text
        if not text:
            await send(wa.text, "I can read text, voice notes, photos and PDF bills. Try “what should I cook?” or send a photo of your groceries.")
            return
        replies: list[str] = []
        turn = ordering.current_turn.set({"id": new_id(), "text": text})
        try:
            async with Client(mcp) as client:
                async for event in _turn_events({"text": text}, client):
                    if event["type"] == "reply" and event.get("text"):
                        replies.append(event["text"])
        finally:
            ordering.current_turn.reset(turn)
        await send(wa.text, "\n\n".join(replies) or "Sorry, I didn't get an answer for that. Please try again.")

    @mcp.custom_route("/api/whatsapp/webhook", methods=["GET", "POST"])
    async def meta_webhook(request: Request) -> Response:
        """Meta's Cloud API webhook (test number): GET is the subscription handshake; POST carries
        messages, which go to the same SQS inbox as End User Messaging's, so one Lambda answers both."""
        if request.method == "GET":
            q = request.query_params
            if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == os.environ.get("META_WA_VERIFY_TOKEN", "pantry-pilot-webhook"):
                return Response(q.get("hub.challenge", ""), media_type="text/plain")
            return Response("Forbidden", status_code=403)
        body = await request.body()
        if not verify_signature(os.environ.get("META_APP_SECRET", ""), body, request.headers.get("x-hub-signature-256", "")):
            return Response("Bad signature", status_code=401)
        queue = os.environ.get("WHATSAPP_INBOX_QUEUE_URL")
        if queue:
            import boto3

            sqs = boto3.client("sqs")
            for entry in json.loads(body).get("entry", []):
                await anyio.to_thread.run_sync(
                    lambda e=entry: sqs.send_message(QueueUrl=queue, MessageBody=json.dumps({"whatsAppWebhookEntry": e}))
                )
        return Response("OK", media_type="text/plain")

    @mcp.custom_route("/api/whatsapp/preview", methods=["POST"])
    async def whatsapp_preview(request: Request) -> Response:
        """The WhatsApp chat inside the app: {text} | {button} | {image: data URL}. Runs the same
        replies as the real number and returns them instead of sending them."""
        body = await request.json()
        transcript = WhatsAppTranscript()
        base = {"id": new_id(), "sender": "app"}
        if image := str(body.get("image") or ""):
            mime, _, data = image.partition(";base64,")
            mime = mime.removeprefix("data:")
            try:
                transcript.media = base64.b64decode(data)
            except ValueError:
                return JSONResponse({"error": "That file couldn't be read."}, status_code=400)
            kind = "document" if mime == "application/pdf" else "image"
            m = Inbound(**base, kind=kind, mime=mime, media_id="app", text=str(body.get("text") or ""))
        elif button := str(body.get("button") or ""):
            m = Inbound(**base, kind="button", button=button)
        else:
            m = Inbound(**base, kind="text", text=str(body.get("text") or "").strip())
        try:
            await whatsapp_reply(m, transcript)
        except NovaUnavailable as err:
            transcript.text("app", str(err))
        household = json.loads(dumps_for_api(store.load(household_id_now())))
        return JSONResponse({"messages": transcript.messages, "state": household})

    if os.environ.get("WHATSAPP_INBOX") == "on":
        whatsapp = WhatsApp()

        @mcp.custom_route("/events", methods=["POST"])
        async def whatsapp_events(request: Request) -> Response:
            """SQS batches of WhatsApp messages, posted here by the Lambda Web Adapter (inbox Lambda only,
            which has no URL). A failed message is logged and dropped rather than retried, so nobody
            gets the same reply twice."""
            for message in parse_event(await request.json()):
                try:
                    await whatsapp_message(message)
                except Exception:
                    log.exception("WhatsApp message %s failed", message.id)
                    with contextlib.suppress(Exception):
                        await anyio.to_thread.run_sync(
                            whatsapp.text, message.sender, "Sorry, something went wrong on my side. Please try again in a minute."
                        )
            return JSONResponse({"batchItemFailures": []})

        async def whatsapp_message(m: Inbound) -> None:
            seen = f"whatsapp-seen#{m.id}"
            if not m.id or await anyio.to_thread.run_sync(store.load_record, seen):
                return  # SQS delivers at least once
            await anyio.to_thread.run_sync(store.save_record, seen, {"at": time.time()})
            await anyio.to_thread.run_sync(whatsapp.mark_read, m.id)
            link = await anyio.to_thread.run_sync(store.load_record, f"whatsapp#{m.sender}")
            if not link:
                await anyio.to_thread.run_sync(
                    whatsapp.link,
                    m.sender,
                    "Hi! This number isn't linked to Pantry Pilot yet. Open the app, go to Shopping apps, "
                    "and add it under WhatsApp updates. Then message me here any time.",
                    "Open Pantry Pilot",
                    APP_URL,
                )
                return
            user = current_user.set(link["household"])
            try:
                await whatsapp_reply(m, whatsapp)
            finally:
                current_user.reset(user)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        tools = await mcp.list_tools()
        return JSONResponse({
            "ok": True,
            "nova": "bedrock" if isinstance(nova, BedrockNova) else "mock",
            "model": getattr(nova, "model_id", None),
            "tools": len(tools),
        })

    behind_cloudfront = bool(os.environ.get("ORIGIN_SECRET"))
    app_mcp = mcp
    app = mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # DNS-rebinding protection guards servers on a user's own machine. Behind CloudFront the
        # Host header is the Lambda URL's, and the origin secret already limits who can call us.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False)
        if behind_cloudfront
        else TransportSecuritySettings(
            allowed_hosts=["localhost:*", "127.0.0.1:*", *_env_list("ALLOWED_HOSTS")],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*", *_env_list("ALLOWED_ORIGINS")],
        ),
    )
    # The last middleware added runs first: CORS, origin secret, then the sign-in checks.
    if auth:
        key = signing_key or jwks_signing_key(auth)
        mcp_verifier = TokenVerifier(auth, key, scopes=ALL_SCOPES)
        if auth.web_client_id:
            # Clients that signed in on Pantry Pilot's own page (oauth_app) hold the web app's tokens.
            mcp_verifier = EitherVerifier(mcp_verifier, TokenVerifier(auth, key, client_ids={auth.web_client_id}))
        app.add_middleware(BearerAuthMiddleware, verifier=mcp_verifier, protects=is_mcp_path)
        if auth.web_client_id:
            app.add_middleware(
                BearerAuthMiddleware,
                verifier=TokenVerifier(auth, key, client_ids={auth.web_client_id}),
                protects=is_private_api_path,
            )
    if behind_cloudfront:
        app.add_middleware(OriginSecretMiddleware, secret=os.environ["ORIGIN_SECRET"])
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_origins=_env_list("ALLOWED_ORIGINS"),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    app.add_middleware(TimezoneMiddleware)  # the device's time zone, for "which meal is it now"
    app.state.mcp = app_mcp  # the MCP server itself, for in-process clients and tests
    return app
