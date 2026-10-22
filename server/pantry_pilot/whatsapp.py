"""WhatsApp as a second way into Pantry Pilot.

Messages to the Pantry Pilot number reach AWS End User Messaging Social, which publishes them to
SNS; an SQS queue feeds them to the inbox Lambda (the same server, started with WHATSAPP_INBOX=on),
and the Lambda Web Adapter posts each batch to /events. Replies are free-form messages, allowed
for 24 hours after the person writes to us, so they need no Meta template.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Media WhatsApp sends that Nova can read, as the data-URL MIME type scan_pantry expects.
READABLE_MEDIA = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


@dataclass(frozen=True)
class Inbound:
    id: str
    sender: str  # E.164, e.g. +919876543210
    kind: str  # "text" | "image" | "document" | "button" | "other"
    text: str = ""
    media_id: str = ""
    mime: str = ""
    button: str = ""


def parse_event(event: dict[str, Any]) -> list[Inbound]:
    """Messages people sent us in an SQS batch of End User Messaging Social events (SNS envelopes).
    Delivery receipts ("sent", "read") carry no messages and are skipped."""
    found: list[Inbound] = []
    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            if "Message" in body:
                body = json.loads(body["Message"])
            entry = body["whatsAppWebhookEntry"]
            entry = json.loads(entry) if isinstance(entry, str) else entry
        except (KeyError, TypeError, ValueError):
            log.warning("Skipping an unreadable WhatsApp event")
            continue
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                found.append(_inbound(m))
    return found


def _inbound(m: dict[str, Any]) -> Inbound:
    base = {"id": str(m.get("id", "")), "sender": "+" + str(m.get("from", "")).lstrip("+")}
    kind = m.get("type")
    if kind == "text":
        return Inbound(**base, kind="text", text=str(m.get("text", {}).get("body", "")).strip())
    if kind in ("image", "document"):
        media = m.get(kind, {})
        return Inbound(
            **base,
            kind=kind,
            media_id=str(media.get("id", "")),
            mime=str(media.get("mime_type", "")).split(";")[0].strip(),
            text=str(media.get("caption", "")).strip(),
        )
    if kind == "interactive":
        reply = m.get("interactive", {}).get("button_reply", {})
        return Inbound(**base, kind="button", button=str(reply.get("id", "")), text=str(reply.get("title", "")))
    if kind == "button":  # a quick-reply button on a template message
        return Inbound(**base, kind="button", button=str(m.get("button", {}).get("payload", "")), text=str(m.get("button", {}).get("text", "")))
    return Inbound(**base, kind="other")


class WhatsApp:
    """Free-form replies and media downloads through AWS End User Messaging Social."""

    def __init__(self, phone_id: str | None = None, media_bucket: str | None = None) -> None:
        self.phone_id = phone_id or os.environ.get("WHATSAPP_PHONE_ID", "")
        self.media_bucket = media_bucket or os.environ.get("WHATSAPP_MEDIA_BUCKET", "")

    def _send(self, to: str, body: dict[str, Any]) -> None:
        import boto3

        message = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **body}
        boto3.client("socialmessaging").send_whatsapp_message(
            originationPhoneNumberId=self.phone_id, message=json.dumps(message).encode(), metaApiVersion="v20.0"
        )

    def text(self, to: str, text: str) -> None:
        self._send(to, {"type": "text", "text": {"body": text[:4000], "preview_url": True}})

    def buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> None:
        """Up to 3 reply buttons (titles up to 20 characters)."""
        action = {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons[:3]]}
        self._send(to, {"type": "interactive", "interactive": {"type": "button", "body": {"text": text[:1000]}, "action": action}})

    def link(self, to: str, text: str, label: str, url: str) -> None:
        """A message with one button that opens a web page."""
        action = {"name": "cta_url", "parameters": {"display_text": label[:20], "url": url}}
        self._send(to, {"type": "interactive", "interactive": {"type": "cta_url", "body": {"text": text[:1000]}, "action": action}})

    def mark_read(self, message_id: str) -> None:
        import boto3

        try:
            message = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
            boto3.client("socialmessaging").send_whatsapp_message(
                originationPhoneNumberId=self.phone_id, message=json.dumps(message).encode(), metaApiVersion="v20.0"
            )
        except Exception:
            log.info("Couldn't mark a WhatsApp message read", exc_info=True)

    def download(self, media_id: str) -> bytes:
        """The photo or PDF someone sent, via a short-lived object in the media bucket."""
        import boto3

        key = f"inbound/{uuid.uuid4().hex}"
        boto3.client("socialmessaging").get_whatsapp_message_media(
            mediaId=media_id,
            originationPhoneNumberId=self.phone_id,
            destinationS3File={"bucketName": self.media_bucket, "key": key},
        )
        # The service appends "<media id>.<extension>" to the key it's given, so look it up by prefix.
        s3 = boto3.client("s3")
        stored = [o["Key"] for o in s3.list_objects_v2(Bucket=self.media_bucket, Prefix=key).get("Contents", [])]
        if not stored:
            raise RuntimeError(f"WhatsApp media {media_id} wasn't saved to S3")
        try:
            return s3.get_object(Bucket=self.media_bucket, Key=stored[0])["Body"].read()
        finally:
            for k in stored:
                s3.delete_object(Bucket=self.media_bucket, Key=k)


def item_lines(items: list[dict[str, Any]], limit: int = 15) -> str:
    lines = []
    for i in items[:limit]:
        qty = i.get("quantity")
        unit = i.get("unit") or ""
        amount = f"{qty:g} {unit}".strip() if isinstance(qty, (int, float)) else ""
        check = " (please check)" if i.get("confidence", 1) < 0.85 else ""
        lines.append(f"{i.get('emoji') or '•'} {i.get('name')}" + (f", {amount}" if amount else "") + check)
    if len(items) > limit:
        lines.append(f"…and {len(items) - limit} more")
    return "\n".join(lines)
