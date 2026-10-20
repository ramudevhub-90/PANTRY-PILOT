import urllib.parse

from starlette.testclient import TestClient

from pantry_pilot.app import create_app
from pantry_pilot.nova import MockNova
from pantry_pilot.store import MemoryStore


def test_voice_stream_url_is_signed_for_transcribe(monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
    with TestClient(create_app(MemoryStore(), MockNova(), "alice")) as client:
        body = client.get("/api/voice/stream?lang=en-IN").json()
        unknown = client.get("/api/voice/stream?lang=xx-YY").json()

    url = urllib.parse.urlparse(body["url"])
    query = dict(urllib.parse.parse_qsl(url.query))
    assert url.scheme == "wss" and url.netloc == "transcribestreaming.us-east-1.amazonaws.com:8443"
    assert query["language-code"] == "en-IN" and query["media-encoding"] == "pcm" and query["sample-rate"] == "16000"
    assert query["X-Amz-Expires"] == "300" and query["X-Amz-Security-Token"] == "token" and query["X-Amz-Signature"]
    assert "/transcribe/aws4_request" in query["X-Amz-Credential"]
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlparse(unknown["url"]).query))["language-code"] == "en-US"
