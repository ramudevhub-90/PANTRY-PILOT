"""Order notifications: email now, WhatsApp once the household's number is set up.

Emails go through the Notify function (infra/lambda/auth/notify.mjs), which sends with SES in the
account that is out of the sandbox. The recipient is the signed-in user's own email address from
Cognito, never an address taken from a request.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class NotifyUnavailable(Exception):
    """Email isn't configured here (local development) or the address can't be found."""


@dataclass
class Message:
    subject: str
    heading: str
    intro: str
    items: list[dict[str, str]] = field(default_factory=list)
    """{"name", "quantity"?, "price"?} rows."""
    total: str | None = None
    button: dict[str, str] | None = None
    """{"label", "url"}; only https links are rendered."""
    footer: str = ""


class Notifier:
    def __init__(
        self,
        email_of: Callable[[str], str | None] | None = None,
        send_email: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._email_of = email_of or _cognito_email
        self._send_email = send_email or _invoke_notify

    def email(self, user: str, message: Message) -> str:
        """Email the user; returns the address it went to."""
        address = self._email_of(user)
        if not address:
            raise NotifyUnavailable("I couldn't find your email address.")
        self._send_email({"to": address, **message.__dict__})
        return address


def _cognito_email(sub: str) -> str | None:
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    if not pool:
        raise NotifyUnavailable("Email isn't set up on this server.")
    import boto3

    found = boto3.client("cognito-idp").list_users(UserPoolId=pool, Filter=f'sub = "{sub}"', Limit=1)["Users"]
    attrs = {a["Name"]: a["Value"] for a in found[0]["Attributes"]} if found else {}
    return attrs.get("email")


def _invoke_notify(payload: dict[str, Any]) -> None:
    function = os.environ.get("NOTIFY_FUNCTION")
    if not function:
        raise NotifyUnavailable("Email isn't set up on this server.")
    import boto3

    response = boto3.client("lambda").invoke(FunctionName=function, Payload=json.dumps(payload).encode())
    if response.get("FunctionError"):
        log.warning("Notify failed: %s", response["Payload"].read()[:300])
        raise NotifyUnavailable("The email couldn't be sent right now.")
