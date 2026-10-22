"""Voice: signed Amazon Transcribe streaming URLs for hands-free, and spoken replies from Amazon Polly.

The browser streams microphone audio straight to Amazon Transcribe over a WebSocket and listens for
"Alexa" in the transcript. That works in every browser, unlike the Web Speech API (missing in
Firefox, short-lived in Safari). The server only signs the URL, with its own AWS credentials, so the
browser never holds AWS keys. A signed URL is good for 5 minutes; an open stream can then run for
hours.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

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
    params: dict[str, Any] = {"media-encoding": "pcm", "sample-rate": SAMPLE_RATE}
    if language == "auto":
        # Hear English and Hindi (and Hinglish) without being told which: Transcribe picks per stream.
        params |= {"identify-language": "true", "language-options": "en-IN,hi-IN", "preferred-language": "en-IN"}
    else:
        params["language-code"] = language if language in LANGUAGES else "en-US"
    query = urllib.parse.urlencode(params)
    request = AWSRequest(
        method="GET",
        url=f"https://transcribestreaming.{region}.amazonaws.com:8443/stream-transcription-websocket?{query}",
    )
    SigV4QueryAuth(credentials.get_frozen_credentials(), "transcribe", region, expires=URL_SECONDS).add_auth(request)
    return "wss://" + request.url.removeprefix("https://")


# ---------- Spoken replies: Amazon Polly ----------
# iPhone Safari's built-in speech often stays silent (blocked, dropped, or routed to the earpiece
# while the microphone is open). An ordinary MP3 from Polly plays reliably everywhere.

POLLY_VOICES = {"en-IN": "Kajal", "hi-IN": "Kajal", "en-US": "Joanna", "en-GB": "Amy", "en-AU": "Olivia"}
MAX_SPEECH_CHARS = 1500
CACHE_ENTRIES = 200


class Speaker:
    """Turns reply text into MP3 with Polly's neural voices. Recent replies are cached per Lambda
    instance, so a repeated phrase is only paid for once."""

    def __init__(self, synthesize: Callable[[str, str, str], bytes] | None = None) -> None:
        self._synthesize = synthesize or _polly_synthesize
        self._cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()

    def speak(self, text: str, language: str = "en-US") -> bytes:
        text = " ".join(text.split())[:MAX_SPEECH_CHARS]
        if not text:
            raise ValueError("Nothing to say.")
        language = language if language in POLLY_VOICES else "en-US"
        key = (language, text)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        audio = self._synthesize(text, POLLY_VOICES[language], language)  # Kajal speaks en-IN and hi-IN
        self._cache[key] = audio
        if len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
        return audio


_polly = None


def _polly_synthesize(text: str, voice_id: str, language: str) -> bytes:
    global _polly
    if _polly is None:
        _polly = boto3.client("polly", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    response = _polly.synthesize_speech(
        Text=text, VoiceId=voice_id, LanguageCode=language, Engine="neural", OutputFormat="mp3", SampleRate="24000"
    )
    return response["AudioStream"].read()


# ---------- Voice notes: Amazon Transcribe batch jobs ----------
# A WhatsApp voice note is a short OGG/Opus file. Streaming wants raw PCM, so a batch job reads it
# from the media bucket instead; a few seconds of speech take about 10-20 seconds.

NOTE_FORMATS = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/aac": "mp4", "audio/amr": "amr", "audio/wav": "wav"}
NOTE_SECONDS = 120


def transcribe_note(audio: bytes, mime: str, bucket: str, region: str | None = None) -> str:
    """What was said in a voice note (English or Hindi, detected), or "" if nothing was heard."""
    fmt = NOTE_FORMATS.get(mime)
    if not fmt or not bucket:
        raise VoiceUnavailable("I can't play that audio format.")
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)
    transcribe = boto3.client("transcribe", region_name=region)
    name = f"pantry-pilot-note-{uuid.uuid4().hex}"
    key = f"voice/{name}.{fmt}"
    s3.put_object(Bucket=bucket, Key=key, Body=audio)
    try:
        transcribe.start_transcription_job(
            TranscriptionJobName=name,
            Media={"MediaFileUri": f"s3://{bucket}/{key}"},
            MediaFormat=fmt,
            IdentifyLanguage=True,
            LanguageOptions=["en-IN", "hi-IN", "en-US"],
        )
        deadline = time.time() + NOTE_SECONDS
        while True:
            job = transcribe.get_transcription_job(TranscriptionJobName=name)["TranscriptionJob"]
            status = job["TranscriptionJobStatus"]
            if status == "COMPLETED":
                with urllib.request.urlopen(job["Transcript"]["TranscriptFileUri"], timeout=15) as response:
                    result = json.loads(response.read())
                return " ".join(t["transcript"] for t in result["results"]["transcripts"]).strip()
            if status == "FAILED":
                raise VoiceUnavailable(job.get("FailureReason") or "Transcription failed")
            if time.time() > deadline:
                raise VoiceUnavailable("Transcription took too long")
            time.sleep(1.5)
    finally:
        s3.delete_object(Bucket=bucket, Key=key)
        try:
            transcribe.delete_transcription_job(TranscriptionJobName=name)
        except Exception:  # noqa: BLE001 - still running or already gone; the job expires on its own
            pass
