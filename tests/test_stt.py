"""Grok STT HTTP client: multipart shape, parsing, cache, key and retries. No network."""
from __future__ import annotations

import json

import pytest

from video_engine import audio
from video_engine.ai import base
from video_engine.util import EngineError

OK = {"text": "Halo dunia", "language": "id", "duration": 1.2,
      "words": [{"text": "Halo", "start": 0.1, "end": 0.5},
                {"text": "dunia", "start": 0.6, "end": 1.1}]}


class Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def voiceover(tmp_path, name="vo.wav", data=b"RIFF-test"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def stub(monkeypatch, *responses):
    seen = []

    def post(url, headers, fields, timeout):
        seen.append({"url": url, "headers": headers, "fields": fields, "timeout": timeout})
        r = responses[min(len(seen) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(audio, "_post", post)
    monkeypatch.setattr(audio, "_wav_16k", lambda p: b"RIFF-wav")
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    return seen


def test_good_words_parsed(monkeypatch, tmp_path):
    seen = stub(monkeypatch, Resp(200, OK))
    words, hit = audio.transcribe_words(voiceover(tmp_path), use_cache=False)
    assert words == [{"word": "Halo", "start": 0.1, "end": 0.5},
                     {"word": "dunia", "start": 0.6, "end": 1.1}]
    assert hit is False
    assert seen[0]["url"] == "https://api.x.ai/v1/stt"
    assert seen[0]["headers"]["Authorization"] == "Bearer test-key"
    assert seen[0]["timeout"] == 300


def test_good_cache_hit_skips_the_request(monkeypatch, tmp_path):
    seen = stub(monkeypatch, Resp(200, OK))
    vo = voiceover(tmp_path, data=b"RIFF-cache")
    first, hit1 = audio.transcribe_words(vo)
    second, hit2 = audio.transcribe_words(vo)
    assert first == second and hit1 is False and hit2 is True and len(seen) == 1


def test_bad_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setattr(audio, "_post", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(EngineError, match="XAI_API_KEY is not set"):
        audio.transcribe_words(voiceover(tmp_path, data=b"RIFF-nokey"), use_cache=False)


def test_good_retry_on_503(monkeypatch, tmp_path):
    seen = stub(monkeypatch, Resp(503), Resp(503), Resp(200, OK))
    words, hit = audio.transcribe_words(voiceover(tmp_path, data=b"RIFF-retry"), use_cache=False)
    assert [w["word"] for w in words] == ["Halo", "dunia"] and hit is False and len(seen) == 3


def test_good_file_is_last_multipart_field(monkeypatch, tmp_path):
    seen = stub(monkeypatch, Resp(200, OK))
    audio.transcribe_words(voiceover(tmp_path, data=b"RIFF-fields"), use_cache=False)
    fields = seen[0]["fields"]
    assert fields[-1][0] == "file"
    assert fields[-1][1][2] == "audio/wav"
    assert [k for k, _ in fields[:-1]] == ["model", "language", "filler_words"]


def test_good_keyterms_repeated(monkeypatch, tmp_path):
    seen = stub(monkeypatch, Resp(200, OK))
    audio.transcribe_words(voiceover(tmp_path, data=b"RIFF-keyterms"), keyterms=["Mallaby", "Surabaya"],
                           use_cache=False)
    fields = seen[0]["fields"]
    assert [v for k, v in fields if k == "keyterm"] == [(None, "Mallaby"), (None, "Surabaya")]
    assert fields[-1][0] == "file"
