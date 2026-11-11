"""Gemini director with a mock client: model fallback, config, JSON parsing, file delete, QA mapping. No network."""
from types import SimpleNamespace

import pytest

from video_engine.ai import base, tasks
from video_engine.ai import gemini as ai
from video_engine.util import EngineError


class APIErr(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


class FakeModels:
    """script[model] is one response or a list consumed per call (last item repeats)."""
    def __init__(self, script):
        self.script, self.calls = dict(script), []

    def generate_content(self, model, contents, config):
        self.calls.append((model, config))
        r = self.script[model]
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(text=r, usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5,
                                                                      thoughts_token_count=None, total_token_count=15))


def client(script):
    return SimpleNamespace(models=FakeModels(script))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(base, "_sleep", slept.append)
    return slept


def test_good_primary_succeeds_after_503s(no_sleep):
    c = client({"gemini-3.8-flash": [APIErr(503), APIErr(503), '{"ok": 1}'], "b": '{"no": 1}'})
    r = ai.generate_json(["gemini-3.8-flash", "b"], ["x"], client=c)
    assert r.model == "gemini-3.8-flash" and r.retries == 2 and not r.fallback and r.warning is None
    assert [m for m, _ in c.models.calls] == ["gemini-3.8-flash"] * 3
    assert len(no_sleep) == 2 and 2.0 <= no_sleep[0] <= 2.5 and 5.0 <= no_sleep[1] <= 6.25


def test_good_primary_exhausts_retries_then_fallback_warns(no_sleep, capsys):
    c = client({"a": [APIErr(429), APIErr(503), APIErr(503), APIErr(503)], "b": '{"v": 2}'})
    r = ai.generate_json(["a", "b"], ["x"], client=c)
    assert [m for m, _ in c.models.calls] == ["a"] * 4 + ["b"]       # 1 try + 3 retries, then fallback
    assert r.model == "b" and r.fallback and r.retries == 0 and "FALLBACK" in r.warning
    assert [t[1] for t in r.tried] == ["error 429", "error 503", "error 503", "error 503", "ok"]
    assert len(no_sleep) == 3 and 12.0 <= no_sleep[2] <= 15.0
    assert "FALLBACK" in capsys.readouterr().err
    assert r.meta()["model"] == "b" and r.meta()["fallback"] is True


def test_bad_400_is_not_retried(no_sleep):
    c = client({"a": APIErr(400), "b": "{}"})
    with pytest.raises(EngineError, match="400"):
        ai.generate_json(["a", "b"], ["x"], client=c)
    assert [m for m, _ in c.models.calls] == ["a"] and no_sleep == []


def test_good_status_string_errors_are_classified(no_sleep):
    e = Exception("x")
    e.status = "UNAVAILABLE"
    c = client({"a": [e, '{"k": 1}']})
    assert ai.generate_json(["a"], ["x"], client=c).retries == 1


def test_good_transport_drop_retried_like_503(no_sleep):
    class RemoteProtocolError(Exception):
        pass
    c = client({"a": [RemoteProtocolError("Server disconnected"), '{"k": 1}'], "b": "{}"})
    r = ai.generate_json(["a", "b"], ["x"], client=c)
    assert r.model == "a" and r.retries == 1 and "transport RemoteProtocolError" in r.tried[0][1]


def test_bad_unknown_exception_not_retried(no_sleep):
    c = client({"a": [ValueError("bad arg"), "{}"]})
    with pytest.raises(EngineError, match="ValueError"):
        ai.generate_json(["a"], ["x"], client=c)


def test_good_404_skips_to_next_model_without_retry(no_sleep):
    c = client({"a": APIErr(404), "b": '{"v": 1}'})
    r = ai.generate_json(["a", "b"], ["x"], client=c)
    assert r.model == "b" and no_sleep == [] and r.fallback


def test_good_config_low_res_thinking_json():
    c = client({"a": "{}"})
    ai.generate_json(["a"], ["x"], schema=tasks.SHOTS_SCHEMA, client=c)
    cfg = c.models.calls[0][1]
    assert cfg.response_mime_type == "application/json" and cfg.thinking_config.thinking_budget == 1024
    assert "LOW" in str(cfg.media_resolution) and cfg.response_json_schema == tasks.SHOTS_SCHEMA


def test_good_invalid_json_retries_same_model_then_next():
    r = ai.generate_json(["a", "b"], ["x"], client=client({"a": ["not json", '{"v": 1}'], "b": '{"v": 2}'}))
    assert r.model == "a" and r.retries == 1
    r = ai.generate_json(["a", "b"], ["x"], client=client({"a": "not json", "b": '{"v": 2}'}))
    assert r.model == "b" and [t[1] for t in r.tried].count("invalid json") == 4


def test_bad_non_retry_error_raises():
    with pytest.raises(EngineError, match="400"):
        ai.generate_json(["a", "b"], ["x"], client=client({"a": APIErr(400), "b": "{}"}))


def test_bad_all_models_fail():
    with pytest.raises(EngineError, match="all Gemini models failed"):
        ai.generate_json(["a", "b"], ["x"], client=client({"a": APIErr(429), "b": APIErr(503)}))


def test_good_model_lists():
    assert ai.PLANNING_MODELS == ai.QA_MODELS == ["gemini-3.8-flash"]


def test_good_analyze_deletes_file_even_on_failure(monkeypatch, tmp_path, assets):
    deleted = []
    files = SimpleNamespace(upload=lambda file, config: SimpleNamespace(name="files/abc", state=SimpleNamespace(name="ACTIVE")),
                            get=lambda name: SimpleNamespace(name=name, state=SimpleNamespace(name="ACTIVE")),
                            delete=lambda name: deleted.append(name))
    c = SimpleNamespace(models=FakeModels({"a": APIErr(503)}), files=files)
    monkeypatch.setattr(ai, "_client", lambda: c)
    with pytest.raises(EngineError):
        tasks.analyze_footage(assets / "clip_1080x1920_30.mp4", tmp_path, ai.GeminiProvider(models=["a"]), 0, 2)
    assert deleted == ["files/abc"] and not list((tmp_path / "proxy").glob("*.mp4"))


def test_good_proxy_is_360p(tmp_path, assets):
    from video_engine.util import video_info
    vi = video_info(tasks.make_proxy(assets / "clip_1080x1920_30.mp4", tmp_path / "p.mp4", 0, 2))
    assert vi["height"] == 360


def test_QA_mapping_bad_and_good(tmp_path, assets):
    png = tmp_path / "s.png"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64", "-frames:v", "1", str(png)],
                   check=True)
    bad = '{"segments": [{"seg": 1, "overlap": true, "cut_off_text": false, "issues": ["pin over card"]}]}'
    fs = tasks.qa_snapshots({1: str(png)}, ai.GeminiProvider(client=client({"q": bad}), qa_models=["q"]))
    assert fs[0].level == "fail" and fs[0].seg == 1 and "pin over card" in fs[0].message
    good = '{"segments": [{"seg": 1, "overlap": false, "cut_off_text": false, "issues": []}]}'
    for reply, level in ((good, "pass"), (APIErr(503), "warn")):
        prov = ai.GeminiProvider(client=client({"q": reply}), qa_models=["q"])
        assert tasks.qa_snapshots({1: str(png)}, prov)[0].level == level


class FakeFiles:
    """In-memory File API: get on a deleted name raises `gone` (404, or 403 as the real API does) unless `keep`
    (a delete that did not stick). `ghost` keeps deleted names in list() while get already fails."""
    def __init__(self, keep=False, gone=404, ghost=False):
        self.live, self.deleted, self.keep, self.n, self.gone, self.ghost = {}, [], keep, 0, gone, ghost

    def list(self):
        names = list(self.live) + (self.deleted if self.ghost else [])
        return [SimpleNamespace(name=n) for n in names]

    def upload(self, file, config):
        self.n += 1
        f = SimpleNamespace(name=f"files/f{self.n}", state=SimpleNamespace(name="ACTIVE"), uri=f"u{self.n}",
                            mime_type=config["mime_type"])
        self.live[f.name] = f
        return f

    def get(self, name):
        if name not in self.live:
            raise APIErr(self.gone)
        return self.live[name]

    def delete(self, name):
        self.deleted.append(name)
        if not self.keep:
            self.live.pop(name, None)


def test_good_delete_verified_by_404():
    fs = FakeFiles()
    c = SimpleNamespace(files=fs)
    f = fs.upload("x", {"mime_type": "audio/mpeg"})
    d = ai.delete_and_verify(c, f.name)
    assert d["deleted"] and d["verified_gone"] and d["get_status"] == "404" and d["listed"] is False


def test_good_delete_verified_by_403_and_absent_from_list():
    fs = FakeFiles(gone=403)
    f = fs.upload("x", {"mime_type": "audio/mpeg"})
    d = ai.delete_and_verify(SimpleNamespace(files=fs), f.name)
    assert d["verified_gone"] and d["get_status"].startswith("403")


def test_bad_delete_not_verified_when_still_listed():
    fs = FakeFiles(gone=403, ghost=True)
    f = fs.upload("x", {"mime_type": "audio/mpeg"})
    d = ai.delete_and_verify(SimpleNamespace(files=fs), f.name)
    assert not d["verified_gone"] and d["listed"] is True


def test_bad_delete_not_verified_on_other_error():
    fs = FakeFiles(gone=500)
    f = fs.upload("x", {"mime_type": "audio/mpeg"})
    d = ai.delete_and_verify(SimpleNamespace(files=fs), f.name)
    assert not d["verified_gone"] and d["listed"] is None


def test_bad_delete_not_verified_when_file_still_exists():
    fs = FakeFiles(keep=True)
    f = fs.upload("x", {"mime_type": "audio/mpeg"})
    d = ai.delete_and_verify(SimpleNamespace(files=fs), f.name)
    assert d["deleted"] and not d["verified_gone"] and "still exists" in d["get_status"]


def test_good_analyze_records_verified_deletion(tmp_path, assets):
    fs = FakeFiles()
    c = SimpleNamespace(models=FakeModels({"a": '{"summary": "s", "shots": []}'}), files=fs)
    r = tasks.analyze_footage(assets / "clip_1080x1920_30.mp4", tmp_path, ai.GeminiProvider(client=c, models=["a"]),
                           0, 2)
    assert r.usage["file_deletions"][0]["verified_gone"] and fs.live == {}


def test_good_qa_fallback_warns():
    prov = ai.GeminiProvider(client=client({"a": APIErr(404), "b": '{"segments": []}'}), qa_models=["a", "b"])
    fs = tasks.qa_snapshots({}, prov)
    assert [f.level for f in fs] == ["warn", "pass"] and "FALLBACK" in fs[0].message


def test_good_client_has_request_timeout(monkeypatch):
    pytest.importorskip("google.genai")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    c = ai._client()
    assert c._api_client._http_options.timeout == ai.HTTP_TIMEOUT_S * 1000


def test_bad_client_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(EngineError, match="GEMINI_API_KEY"):
        ai._client()


def _genai_installed() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("google.genai") is not None
    except ModuleNotFoundError:
        return False


def test_bad_client_without_package_names_the_extra(monkeypatch):
    if _genai_installed():
        pytest.skip("google-genai is installed; this case is the missing-package error")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    with pytest.raises(EngineError, match="google-genai"):
        ai._client()
