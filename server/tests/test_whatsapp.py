import base64
import json

import pytest
from starlette.testclient import TestClient

from pantry_pilot import app as app_module
from pantry_pilot.app import create_app
from pantry_pilot.notify import Notifier
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore
from pantry_pilot.whatsapp import Inbound, item_lines, parse_event


def sqs(*messages):
    """An SQS batch the way End User Messaging Social delivers it: SNS envelope, webhook entry as a string."""
    entry = {"id": "waba", "changes": [{"field": "messages", "value": {"messages": list(messages)}}]}
    body = {"Type": "Notification", "Message": json.dumps({"context": {}, "whatsAppWebhookEntry": json.dumps(entry)})}
    return {"Records": [{"messageId": "1", "body": json.dumps(body)}]}


def test_parse_text_photo_and_button():
    event = sqs(
        {"id": "m1", "from": "919876543210", "type": "text", "text": {"body": " Hi "}},
        {"id": "m2", "from": "919876543210", "type": "image", "image": {"id": "media-1", "mime_type": "image/jpeg", "caption": "bill"}},
        {"id": "m3", "from": "919876543210", "type": "interactive", "interactive": {"type": "button_reply", "button_reply": {"id": "scan_add", "title": "Add to pantry"}}},
    )
    text, photo, button = parse_event(event)
    assert text == Inbound(id="m1", sender="+919876543210", kind="text", text="Hi")
    assert (photo.kind, photo.media_id, photo.mime, photo.text) == ("image", "media-1", "image/jpeg", "bill")
    assert (button.kind, button.button) == ("button", "scan_add")


def test_delivery_receipts_and_junk_are_skipped():
    receipt = {"Records": [{"body": json.dumps({"Message": json.dumps({"whatsAppWebhookEntry": json.dumps({"changes": [{"value": {"statuses": [{"status": "read"}]}}]})})})}]}
    assert parse_event(receipt) == []
    assert parse_event({"Records": [{"body": "not json"}]}) == []


def test_item_lines_flag_unsure_items():
    lines = item_lines([{"name": "Milk", "emoji": "🥛", "quantity": 2, "unit": "L", "confidence": 0.95}, {"name": "Paneer", "quantity": 1, "unit": "pack", "confidence": 0.5}])
    assert lines == "🥛 Milk, 2 L\n• Paneer, 1 pack (please check)"


class FakeWhatsApp:
    sent: list = []

    def __init__(self, *_, **__):
        pass

    def text(self, to, text):
        FakeWhatsApp.sent.append(("text", to, text))

    def buttons(self, to, text, buttons):
        FakeWhatsApp.sent.append(("buttons", to, text, [b[0] for b in buttons]))

    def link(self, to, text, label, url):
        FakeWhatsApp.sent.append(("link", to, text, url))

    def mark_read(self, _):
        pass

    def download(self, _):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def inbox(monkeypatch):
    monkeypatch.setenv("WHATSAPP_INBOX", "on")
    monkeypatch.setattr(app_module, "WhatsApp", FakeWhatsApp)
    FakeWhatsApp.sent = []
    store = MemoryStore()
    notifier = Notifier(email_of=lambda user: None, send_email=lambda _: None, send_whatsapp=lambda *_: None)
    with TestClient(create_app(store, MockNova(), "alice", notifier=notifier)) as client:
        yield client, store


def test_saving_a_number_links_it_to_the_household(inbox):
    client, store = inbox
    client.post("/api/notify/whatsapp", json={"number": "98765 43210"})
    assert store.load_record("whatsapp#+919876543210") == {"household": "alice"}
    client.post("/api/notify/whatsapp", json={"number": ""})
    assert store.load_record("whatsapp#+919876543210") is None


def test_unlinked_numbers_are_told_how_to_link(inbox):
    client, _ = inbox
    client.post("/events", json=sqs({"id": "m1", "from": "15550001111", "type": "text", "text": {"body": "hi"}}))
    kind, to, text, _ = FakeWhatsApp.sent[-1]
    assert (kind, to) == ("link", "+15550001111") and "isn't linked" in text


def test_hello_shows_the_menu_and_duplicates_are_ignored(inbox):
    client, store = inbox
    store.save_record("whatsapp#+919876543210", {"household": "alice"})
    event = sqs({"id": "m1", "from": "919876543210", "type": "text", "text": {"body": "Hi"}})
    client.post("/events", json=event)
    client.post("/events", json=event)
    assert len(FakeWhatsApp.sent) == 1
    assert FakeWhatsApp.sent[0][0] == "buttons" and FakeWhatsApp.sent[0][3] == ["meal", "list", "scan"]


def test_scan_word_sends_the_camera_link(inbox):
    client, store = inbox
    store.save_record("whatsapp#+919876543210", {"household": "alice"})
    client.post("/events", json=sqs({"id": "m1", "from": "919876543210", "type": "text", "text": {"body": "scan"}}))
    assert FakeWhatsApp.sent[-1][0] == "link" and FakeWhatsApp.sent[-1][3].endswith("/?scan=1")


def test_photo_then_add_puts_items_in_the_pantry(inbox):
    client, store = inbox
    store.save_record("whatsapp#+919876543210", {"household": "alice"})
    before = store.load("alice").inventory
    client.post("/events", json=sqs({"id": "m1", "from": "919876543210", "type": "image", "image": {"id": "x", "mime_type": "image/png"}}))
    kind, _, text, ids = FakeWhatsApp.sent[-1]
    assert kind == "buttons" and ids == ["scan_add", "scan_cancel"] and "I found" in text
    client.post("/events", json=sqs({"id": "m2", "from": "919876543210", "type": "interactive", "interactive": {"button_reply": {"id": "scan_add", "title": "Add to pantry"}}}))
    assert "added" in FakeWhatsApp.sent[-1][2]
    assert store.load("alice").inventory != before


@pytest.fixture
def app_client():
    store = MemoryStore()
    with TestClient(create_app(store, MockNova(), "alice")) as client:
        yield client, store


def test_preview_runs_the_same_replies_as_whatsapp(app_client):
    client, _ = app_client
    menu = client.post("/api/whatsapp/preview", json={"text": "hi"}).json()["messages"]
    assert menu[0]["kind"] == "buttons" and [b["id"] for b in menu[0]["buttons"]] == ["meal", "list", "scan"]
    scan = client.post("/api/whatsapp/preview", json={"button": "scan"}).json()["messages"]
    assert scan[0]["kind"] == "link" and scan[0]["url"].endswith("/?scan=1")


def test_preview_photo_then_add_updates_the_pantry(app_client):
    client, store = app_client
    before = store.load("alice").inventory
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()
    found = client.post("/api/whatsapp/preview", json={"image": png}).json()["messages"]
    assert found[0]["text"].startswith("Got it") and found[-1]["kind"] == "buttons"
    added = client.post("/api/whatsapp/preview", json={"button": "scan_add"}).json()
    assert "added" in added["messages"][-1]["text"] and store.load("alice").inventory != before
    assert added["state"]["inventory"]
