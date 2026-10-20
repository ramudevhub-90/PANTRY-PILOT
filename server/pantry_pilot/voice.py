"""Hands-free voice: signed Amazon Transcribe streaming URLs for the web app.

The browser streams microphone audio straight to Amazon Transcribe over a WebSocket and listens for
"Alexa" in the transcript. That works in every browser, unlike the Web Speech API (missing in
Firefox, short-lived in Safari). The server only signs the URL, with its own AWS credentials, so the
browser never holds AWS keys. A signed URL is good for 5 minutes; an open stream can then run for
hours.
"""

from __future__ import annotations

import os
import urllib.parse

import boto3
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

SAMPLE_RATE = 16000
LANGUAGES = frozenset({"en-US", "en-IN", "en-GB", "en-AU", "hi-IN"})
URL_SECONDS = 300


class VoiceUnavailable(Exception):
    """No AWS credentials to sign with (e.g. local development without a profile)."""


def stream_url(language: str = "en-US", region: str | None = None) -> str:
    """A presigned wss:// URL for Transcribe streaming (16 kHz, 16-bit PCM)."""
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    session = boto3.Session(region_name=region)
    credentials = session.get_credentials()
    if credentials is None:
        raise VoiceUnavailable("Hands-free voice needs AWS credentials on the server.")
    query = urllib.parse.urlencode({
        "language-code": language if language in LANGUAGES else "en-US",
        "media-encoding": "pcm",
        "sample-rate": SAMPLE_RATE,
    })
    request = AWSRequest(
        method="GET",
        url=f"https://transcribestreaming.{region}.amazonaws.com:8443/stream-transcription-websocket?{query}",
    )
    SigV4QueryAuth(credentials.get_frozen_credentials(), "transcribe", region, expires=URL_SECONDS).add_auth(request)
    return "wss://" + request.url.removeprefix("https://")
