import urllib.parse

from starlette.testclient import TestClient

from pantry_pilot import voice
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


def test_spoken_reply_uses_polly_voice_for_language_and_caches():
    calls = []

    def synth(text, voice_id, language):
        calls.append((text, voice_id, language))
        return b"ID3mp3"

    speaker = voice.Speaker(synth)
    with TestClient(create_app(MemoryStore(), MockNova(), "alice", speaker=speaker)) as client:
        first = client.post("/api/voice/speak", json={"text": "  Added   milk. ", "lang": "en-IN"})
        again = client.post("/api/voice/speak", json={"text": "Added milk.", "lang": "en-IN"})
        us = client.post("/api/voice/speak", json={"text": "Added milk.", "lang": "xx-YY"})
        empty = client.post("/api/voice/speak", json={"text": "   "})

    assert first.status_code == 200 and first.headers["content-type"] == "audio/mpeg" and first.content == b"ID3mp3"
    assert again.content == b"ID3mp3"
    assert calls == [("Added milk.", "Kajal", "en-IN"), ("Added milk.", "Joanna", "en-US")]
    assert us.status_code == 200 and empty.status_code == 400


def test_spoken_reply_reports_polly_failure():
    def broken(*_):
        raise RuntimeError("polly down")

    with TestClient(create_app(MemoryStore(), MockNova(), "alice", speaker=voice.Speaker(broken))) as client:
        response = client.post("/api/voice/speak", json={"text": "Hello"})
    assert response.status_code == 503
