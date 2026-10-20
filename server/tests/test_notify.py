from starlette.testclient import TestClient

from pantry_pilot.app import create_app
from pantry_pilot.nova import MockNova
from pantry_pilot.notify import Notifier, NotifyUnavailable
from pantry_pilot.store import MemoryStore


def test_test_email_goes_to_the_signed_in_users_address():
    sent = []
    notifier = Notifier(email_of=lambda user: f"{user}@example.com", send_email=sent.append)
    with TestClient(create_app(MemoryStore(), MockNova(), "alice", notifier=notifier)) as client:
        response = client.post("/api/notify/test")
    assert response.json() == {"sentTo": "alice@example.com"}
    assert sent[0]["to"] == "alice@example.com" and sent[0]["items"][0]["name"] == "Milk 1 L"


def test_test_email_reports_when_email_is_not_set_up():
    def missing(_):
        raise NotifyUnavailable("Email isn't set up on this server.")

    notifier = Notifier(email_of=missing, send_email=lambda _: None)
    with TestClient(create_app(MemoryStore(), MockNova(), "alice", notifier=notifier)) as client:
        response = client.post("/api/notify/test")
    assert response.status_code == 503
