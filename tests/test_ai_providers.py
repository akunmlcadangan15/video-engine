"""AI provider layer: selection precedence, schema check, agent file handoff, OpenAI-compatible transport."""
from __future__ import annotations

import json
import subprocess
import wave

import pytest

from video_engine import ai, config, draft
from video_engine.ai import agent, base, gemini, openai_compat, tasks
from video_engine.ai.base import AwaitingAgent, Image, Media, Text
from video_engine.ai.gemini import GeminiProvider
from video_engine.cli import main
from video_engine.util import EngineError

from test_ai_director import APIErr, client as gemini_client
from test_draft import client as draft_client, good_plan, proj, storyboards  # noqa: F401  (fixture)

SECRET = "sk-test-SECRET-value-0000"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(base, "_sleep", lambda s: None)


def write_config(tmp_path, monkeypatch, text):
    p = tmp_path / "ai_providers.toml"
    p.write_text(text)
    monkeypatch.setenv(ai.ENV_CONFIG, str(p))
    return p


LLM = """default = "{default}"
[providers.myllm]
base_url = "https://llm.example/v1/"
models = ["big", "small"]
api_key_env = "MYLLM_KEY"
capabilities = ["image"]
"""

GEMINI_DS = """default = "gemini"
[gemini]
fallback_provider = "deepseek"
[providers.deepseek]
base_url = "https://ds.example/v1"
models = ["ds-model"]
qa_models = ["ds-model"]
api_key_env = "OPENGATEWAY_API_KEY"
capabilities = []
json_mode = "json_object"
"""


# ----------------------------------------------------------------------------------------------------- selection
def test_good_precedence_flag_env_config_default(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, LLM.format(default="myllm"))
    monkeypatch.setenv(ai.ENV_PROVIDER, "agent")
    assert ai.resolve("gemini") == "gemini"                      # flag beats env
    assert ai.resolve(None) == "agent"                           # env beats config
    monkeypatch.delenv(ai.ENV_PROVIDER)
    assert ai.resolve(None) == "myllm"                           # config beats default
    monkeypatch.setenv(ai.ENV_CONFIG, str(tmp_path / "absent.toml"))
    assert ai.resolve(None) == "gemini"                          # default


def test_good_get_builds_each_provider(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, LLM.format(default="gemini"))
    assert isinstance(ai.get(), GeminiProvider)
    assert isinstance(ai.get("agent", root=tmp_path), agent.AgentProvider)
    p = ai.get("myllm")
    assert isinstance(p, openai_compat.OpenAICompatProvider) and p.entry.base_url == "https://llm.example/v1"
    assert p.models == {"planning": ["big", "small"], "qa": ["big", "small"]}


def test_bad_unknown_provider_names_known_ones(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, LLM.format(default="gemini"))
    with pytest.raises(EngineError, match="unknown AI provider 'nope'.*gemini, agent, myllm"):
        ai.resolve("nope")


def test_bad_config_entry_and_toml_errors(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, '[providers.x]\nbase_url = "http://h"\n')
    with pytest.raises(EngineError, match="missing models, api_key_env"):
        ai.get("x")
    write_config(tmp_path, monkeypatch, '[providers.x]\nbase_url = "http://h"\nmodels=["m"]\napi_key_env="K"\n'
                                        'capabilities=["smell"]\n')
    with pytest.raises(EngineError, match="unknown capabilities"):
        ai.get("x")
    write_config(tmp_path, monkeypatch, "default = \n")
    with pytest.raises(EngineError, match="invalid TOML"):
        ai.resolve(None)


def test_good_repo_config_parses_and_defaults_to_gemini():
    import tomllib
    from video_engine import config
    cfg = tomllib.loads((config.ENGINE_ROOT / "ai_providers.toml").read_text())
    assert ai.resolve(None, cfg) == "gemini" and cfg["agent"]["model_label"] == agent.MODEL_LABEL


# --------------------------------------------------------------------------------------------------------- schema
SCH = {"type": "object", "required": ["n", "xs"], "properties": {
    "n": {"type": "integer"}, "k": {"type": "string", "enum": ["a", "b"]}, "f": {"type": "number"},
    "xs": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "object", "required": ["b"],
                                                                    "properties": {"b": {"type": "boolean"}}}}}}


def test_good_schema_valid():
    assert base.schema_errors({"n": 3, "k": "a", "f": 1, "xs": [{"b": True}]}, SCH) == []
    assert base.schema_errors({"n": 3.0, "xs": [{"b": False}]}, SCH) == []


def test_bad_schema_errors_are_pathed():
    errs = base.schema_errors({"n": True, "k": "z", "f": "1", "xs": [{"b": 1}, {}, {}]}, SCH)
    joined = "\n".join(errs)
    for want in ("$.n: expected integer", "$.k: 'z' not in", "$.f: expected number", "$.xs: more than 2",
                 "$.xs[0].b: expected boolean", "$.xs[1]: missing required 'b'"):
        assert want in joined, want
    assert base.schema_errors({}, SCH) == ["$: missing required 'n'", "$: missing required 'xs'"]
    assert base.schema_errors({"n": 1.5, "xs": []}, SCH) == ["$.n: expected integer, got float", "$.xs: fewer than 1 items"]


# ---------------------------------------------------------------------------------------------- agent: draft flow
def bundle(p, rnd=0):
    return p / "ai_request" / "draft" / str(rnd)


AGENT = ["--provider", "agent", "--no-dom"]


def start(p, extra=()):
    """Fresh agent draft: the storyboard bundle is answered, the resume stops at the first draft bundle (exit 3)."""
    args = ["draft", str(p), *AGENT, *extra]
    assert main(args) == 3
    sb = p / "ai_request" / "draft" / "storyboard"
    assert "STORYBOARD REQUEST" in (sb / "request.md").read_text()
    (sb / "response.json").write_text(json.dumps(storyboards()))
    return main(args + ["--resume"])


def test_good_agent_draft_bundle_and_exit_code(proj, capsys):
    assert start(proj) == agent.EXIT_AWAITING
    d = bundle(proj)
    names = {x.name for x in d.iterdir()}
    assert {"request.md", "schema.json", "transcript.json", "inventory.json", "credits.json", "images",
            "contact_sheet.jpg"} <= names and "response.json" not in names
    md = (d / "request.md").read_text()
    assert "RULES" in md and "CREDITS.TXT LINES" in md and "![THUMBNAIL still](images/" in md
    assert f"engine draft {proj} --provider agent --no-dom --resume" in md
    assert json.loads((d / "schema.json").read_text()) == draft.schema(draft.collect(proj))
    words = json.loads((d / "transcript.json").read_text())
    assert words and {"word", "start_frame", "end_frame"} <= set(words[0])
    assert {a["id"] for a in json.loads((d / "inventory.json").read_text())} >= {"still", "clip_1080x1920_30"}
    imgs = sorted((d / "images").iterdir())
    assert imgs and all((d / "images" / i.name).read_bytes()[:2] == b"\xff\xd8" for i in imgs)
    out = capsys.readouterr().out
    assert "waiting for agent response" in out and str(d / "response.json") in out
    rep = json.loads((proj / "draft_report.json").read_text())
    assert rep["provider"] == "agent" and rep["awaiting"] == str(d) and rep["ok"] is False
    assert not (proj / "master_timeline.draft.json").exists()


def test_good_agent_resume_with_good_response_passes_gates(proj, capsys):
    assert start(proj) == 3
    (bundle(proj) / "response.json").write_text(json.dumps(good_plan()))
    assert main(["draft", str(proj), "--provider", "agent", "--no-dom", "--resume"]) == 0
    rep = json.loads((proj / "draft_report.json").read_text())
    assert rep["ok"] and rep["provider"] == "agent" and rep["model"] == agent.MODEL_LABEL and rep["repair_rounds"] == 0
    assert rep["rounds"][0]["ai"]["provider"] == "agent" and rep["file_deletions"] == []
    assert (proj / "master_timeline.draft.json").is_file()
    assert "provider agent model grok-bot" in capsys.readouterr().out


def test_bad_agent_response_failing_gates_writes_next_round(proj):
    bad = good_plan()
    bad["segments"][1].update(asset="clip_1280x720_25")                     # no credit line -> gate error
    assert start(proj) == 3
    (bundle(proj) / "response.json").write_text(json.dumps(bad))
    assert main(["draft", str(proj), "--provider", "agent", "--no-dom", "--resume"]) == 3
    d1 = bundle(proj, 1)
    assert "no credit line" in (d1 / "gate_errors.txt").read_text()
    md = (d1 / "request.md").read_text()
    assert "THE ENGINE REJECTED IT WITH THESE GATE ERRORS" in md and "no credit line" in md
    assert json.loads((d1 / "previous_response.json").read_text()) == bad
    (d1 / "response.json").write_text(json.dumps(good_plan()))
    assert main(["draft", str(proj), "--provider", "agent", "--no-dom", "--resume"]) == 0
    rep = json.loads((proj / "draft_report.json").read_text())
    assert rep["ok"] and rep["repair_rounds"] == 1


def test_bad_agent_repairs_exhausted_is_rejected(proj):
    bad = good_plan()
    bad["segments"][1].update(asset="clip_1280x720_25")
    args = ["draft", str(proj), "--provider", "agent", "--no-dom", "--max-repairs", "0"]
    assert start(proj, ["--max-repairs", "0"]) == 3
    (bundle(proj) / "response.json").write_text(json.dumps(bad))
    assert main(args + ["--resume"]) == 1
    rep = json.loads((proj / "draft_report.json").read_text())
    assert not rep["ok"] and "no credit line" in rep["rounds"][0]["errors"][0] and not bundle(proj, 1).exists()


def test_bad_agent_resume_missing_response(proj, capsys):
    assert start(proj) == 3
    assert main(["draft", str(proj), "--provider", "agent", "--no-dom", "--resume"]) == 1
    err = capsys.readouterr().err
    assert "no response.json in" in err and str(bundle(proj)) in err


@pytest.mark.parametrize("text,msg", [("{not json", "invalid JSON"), ("[1]", "top level must be a JSON object"),
                                      ('{"segments": "x"}', "does not match schema.json")])
def test_bad_agent_invalid_response(proj, capsys, text, msg):
    assert start(proj) == 3
    (bundle(proj) / "response.json").write_text(text)
    assert main(["draft", str(proj), "--provider", "agent", "--no-dom", "--resume"]) == 1
    err = capsys.readouterr().err
    assert msg in err and "response.json" in err


def test_good_agent_fresh_run_drops_old_bundles(proj):
    assert start(proj) == 3
    (bundle(proj) / "response.json").write_text(json.dumps(good_plan()))
    assert start(proj) == 3
    assert not (bundle(proj) / "response.json").exists()


def test_good_clean_removes_project_bundles(proj):
    assert start(proj) == 3
    assert main(["clean", str(proj)]) == 0
    assert not (proj / "ai_request").exists()


# ------------------------------------------------------------------------------------- agent: director and ai-qa
def test_good_agent_director_round_trip(tmp_path, assets):
    src = assets / "clip_1080x1920_30.mp4"
    prov = agent.AgentProvider(tmp_path / "req", "engine director X --provider agent --resume")
    with pytest.raises(AwaitingAgent) as ei:
        tasks.analyze_footage(src, tmp_path / "w", prov, 0, 2)
    d = ei.value.bundle
    assert d == tmp_path / "req" / tasks.director_request_id(src, 0)
    assert len(list((d / "images").glob("*.jpg"))) == tasks.DIRECTOR_FRAMES and (d / "contact_sheet.jpg").is_file()
    assert "t=" in (d / "request.md").read_text() and not list((tmp_path / "w" / "proxy").glob("*.mp4"))
    ans = {"summary": "s", "shots": [{"start_s": 0, "end_s": 2, "description": "d", "focus_box": [0, 0, 1000, 1000],
                                      "suggested_camera": "static", "has_watermark_or_text": False}]}
    (d / "response.json").write_text(json.dumps(ans))
    r = tasks.analyze_footage(src, tmp_path / "w", agent.AgentProvider(tmp_path / "req", "", resume=True), 0, 2)
    assert r.data == ans and r.provider == "agent" and r.usage["file_deletions"] == []


def test_bad_agent_director_shape_error(tmp_path, assets):
    src = assets / "clip_1080x1920_30.mp4"
    prov = agent.AgentProvider(tmp_path, "")
    with pytest.raises(AwaitingAgent) as ei:
        tasks.analyze_footage(src, tmp_path / "w", prov, 0, 2)
    (ei.value.bundle / "response.json").write_text('{"summary": "s", "shots": [{"start_s": 0}]}')
    with pytest.raises(EngineError, match="missing required 'focus_box'"):
        tasks.analyze_footage(src, tmp_path / "w", agent.AgentProvider(tmp_path, "", resume=True), 0, 2)


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "s.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64", "-frames:v", "1", str(p)],
                   check=True)
    return p


def test_good_and_bad_agent_ai_qa(tmp_path, png):
    prov = agent.AgentProvider(tmp_path, "engine preflight P --ai-qa --provider agent --resume")
    with pytest.raises(AwaitingAgent) as ei:
        tasks.qa_snapshots({1: str(png), 2: str(png)}, prov)
    d = ei.value.bundle
    assert d == tmp_path / "aiqa" / "0" and len(list((d / "images").glob("*.png"))) == 2
    assert "overlap=true" in (d / "request.md").read_text()
    good = {"segments": [{"seg": 1, "overlap": False, "cut_off_text": False, "issues": []}]}
    (d / "response.json").write_text(json.dumps(good))
    meta: dict = {}
    fs = tasks.qa_snapshots({1: str(png)}, agent.AgentProvider(tmp_path, "", resume=True), meta=meta)
    assert [f.level for f in fs] == ["pass"] and meta["provider"] == "agent"
    bad = {"segments": [{"seg": 2, "overlap": True, "cut_off_text": False, "issues": ["pin over card"]}]}
    (d / "response.json").write_text(json.dumps(bad))
    fs = tasks.qa_snapshots({1: str(png)}, agent.AgentProvider(tmp_path, "", resume=True))
    assert fs[0].level == "fail" and fs[0].seg == 2 and "pin over card" in fs[0].message
    (d / "response.json").write_text('{"segments": [{"seg": 1}]}')
    with pytest.raises(EngineError, match="does not match schema.json"):
        tasks.qa_snapshots({1: str(png)}, agent.AgentProvider(tmp_path, "", resume=True))


# --------------------------------------------------------------------------------------------- openai_compatible
class FakePost:
    """script[model]: list of (status, body) or Exception, consumed per call (last repeats)."""
    def __init__(self, script):
        self.script, self.calls = {k: list(v) for k, v in script.items()}, []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
        q = self.script[payload["model"]]
        r = q.pop(0) if len(q) > 1 else q[0]
        if isinstance(r, Exception):
            raise r
        return r


def ok(obj, usage=None):
    return 200, {"choices": [{"message": {"content": json.dumps(obj)}}], "usage": usage or {"total_tokens": 7}}


def entry(**kw):
    d = {"base_url": "https://llm.example/v1", "models": ["big", "small"], "api_key_env": "MYLLM_KEY",
         "capabilities": ["image"], **kw}
    return openai_compat.Entry.from_config("myllm", d)


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("MYLLM_KEY", SECRET)


def test_good_openai_request_shape(key, tmp_path):
    post = FakePost({"big": [ok({"n": 1, "xs": [{"b": True}]})]})
    p = openai_compat.OpenAICompatProvider(entry(), post)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    parts = [Text("hello"), Image(b"\xff\xd8jpg", "image/jpeg", "t1"),
             Media(wav, "audio", "audio/wav", "narration", transcript="[0]halo")]
    r = p.generate_json("planning", parts, SCH, "r")
    c = post.calls[0]
    assert c["url"] == "https://llm.example/v1/chat/completions" and c["headers"]["Authorization"] == f"Bearer {SECRET}"
    content = c["payload"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "hello"}
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[2]["type"] == "text" and "[0]halo" in content[2]["text"]           # no audio capability
    rf = c["payload"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["schema"] == SCH
    assert r.data == {"n": 1, "xs": [{"b": True}]} and r.provider == "myllm" and r.model == "big"
    assert r.usage == {"total_tokens": 7} and c["timeout"] == base.HTTP_TIMEOUT_S


def test_good_openai_json_object_mode_and_fenced_reply(key):
    post = FakePost({"big": [(200, {"choices": [{"message": {"content": '```json\n{"v": 2}\n```'}}]})]})
    p = openai_compat.OpenAICompatProvider(entry(json_mode="json_object"), post)
    assert p.generate_json("planning", [Text("x")], SCH, "r").data == {"v": 2}
    pl = post.calls[0]["payload"]
    assert pl["response_format"] == {"type": "json_object"}
    assert "Return JSON matching this schema" in pl["messages"][0]["content"][-1]["text"]


def test_good_openai_retry_then_fallback(key):
    import httpx
    post = FakePost({"big": [(503, {}), httpx.ReadTimeout("t"), (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
                             (503, {})], "small": [ok({"v": 1})]})
    r = openai_compat.OpenAICompatProvider(entry(), post).generate_json("planning", [Text("x")], None, "r")
    assert r.model == "small" and r.fallback and "FALLBACK" in r.warning
    assert [m for m, _ in r.tried] == ["big"] * 4 + ["small"]
    assert "transport ReadTimeout" in r.tried[1][1]


def test_good_openai_404_skips_to_next_and_bad_json_retries(key):
    post = FakePost({"big": [(404, {})], "small": [(200, {"choices": [{"message": {"content": "nope"}}]}), ok({"v": 3})]})
    r = openai_compat.OpenAICompatProvider(entry(), post).generate_json("planning", [Text("x")], None, "r")
    assert r.data == {"v": 3} and r.tried == [("big", "error 404"), ("small", "invalid json"), ("small", "ok")]


def test_bad_openai_400_not_retried_and_no_secret(key, capsys):
    post = FakePost({"big": [(400, {"error": {"message": "bad"}})], "small": [ok({})]})
    with pytest.raises(EngineError, match="not retried") as ei:
        openai_compat.OpenAICompatProvider(entry(), post).generate_json("planning", [Text("x")], None, "r")
    assert len(post.calls) == 1 and SECRET not in str(ei.value)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


def test_bad_openai_missing_key_env(monkeypatch, capsys):
    monkeypatch.delenv("MYLLM_KEY", raising=False)
    post = FakePost({"big": [ok({})]})
    with pytest.raises(EngineError, match="environment variable MYLLM_KEY is not set"):
        openai_compat.OpenAICompatProvider(entry(), post).generate_json("planning", [Text("x")], None, "r")
    assert post.calls == []


def test_good_openai_capability_fallback_to_frames(key, assets):
    clip = assets / "clip_1080x1920_30.mp4"
    parts = [Media(clip, "video", "video/mp4", "proxy clip", frames=3)]
    no_video = openai_compat.OpenAICompatProvider(entry(), FakePost({}))
    c = no_video.content(parts)
    assert [x["type"] for x in c] == ["text", "image_url"] * 3 and "frame at" in c[0]["text"]
    with_video = openai_compat.OpenAICompatProvider(entry(capabilities=["image", "video", "audio"]), FakePost({}))
    assert [x["type"] for x in with_video.content(parts)] == ["video_url"]
    text_only = openai_compat.OpenAICompatProvider(entry(capabilities=[]), FakePost({}))
    c = text_only.content(parts + [Image(b"x", "image/png", "snap")])
    assert {x["type"] for x in c} == {"text"} and all("image omitted" in x["text"] for x in c)


def test_good_openai_audio_capability_sends_input_audio(key, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFFdata")
    p = openai_compat.OpenAICompatProvider(entry(capabilities=["image", "audio"]), FakePost({}))
    c = p.content([Media(wav, "audio", "audio/wav", "n", transcript="t")])
    assert c == [{"type": "input_audio", "input_audio": {"data": "UklGRmRhdGE=", "format": "wav"}}]


def test_good_openai_other_audio_containers_are_transcoded_to_wav(key, tmp_path):
    """WAV and MP3 are sent as-is. Any other container is transcoded to 16 kHz mono WAV; a non-audio file fails."""
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 400)
    flac = tmp_path / "a.flac"
    enc = subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", str(wav), str(flac)], capture_output=True)
    assert enc.returncode == 0, enc.stderr
    raw, fmt = openai_compat._audio_payload(flac)
    assert fmt == "wav" and raw.startswith(b"RIFF")
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(b"ID3not-really")
    assert openai_compat._audio_payload(mp3) == (b"ID3not-really", "mp3")
    bad = tmp_path / "nope.ogg"
    bad.write_bytes(b"not audio")
    with pytest.raises(EngineError, match="audio transcode failed"):
        openai_compat._audio_payload(bad)


def test_good_openai_draft_end_to_end(proj, key):
    post = FakePost({"big": [ok(good_plan())]})
    r = draft.draft(proj, provider=openai_compat.OpenAICompatProvider(entry(), post), dom=False)
    assert r.ok and r.report["provider"] == "myllm" and r.report["model"] == "big"
    content = post.calls[0]["payload"]["messages"][0]["content"]
    assert any(x["type"] == "image_url" for x in content)
    assert any(x["type"] == "text" and "audio replaced by its transcript" in x["text"] for x in content)
    assert SECRET not in (proj / "draft_report.json").read_text()


def test_good_gemini_is_default_for_draft_cli(proj, monkeypatch):
    seen = {}

    def fake(self, task, parts, schema, request_id, attachments=None):
        seen.update(task=task, rid=request_id, kinds=[type(x).__name__ for x in parts])
        raise EngineError("stop")
    monkeypatch.setattr(GeminiProvider, "generate_json", fake)
    assert main(["draft", str(proj), "--no-dom"]) == 1
    assert seen["task"] == "planning" and seen["rid"] == "draft/storyboard" and seen["kinds"][:2] == ["Text", "Media"]
    assert json.loads((proj / "draft_report.json").read_text())["provider"] == "gemini"


def test_good_cli_preflight_ai_qa_agent_handoff(project, capsys):
    p, _ = project()
    args = ["preflight", str(p), "--no-dom", "--ai-qa", "--provider", "agent"]
    assert main(args) == agent.EXIT_AWAITING
    d = p / "ai_request" / "aiqa" / "0"
    segs = sorted(int(x.name.split("_")[-1].split(".")[0]) for x in (d / "images").glob("*.png"))
    assert segs and (d / "schema.json").is_file()
    resp = {"segments": [{"seg": s, "overlap": False, "cut_off_text": False, "issues": []} for s in segs]}
    (d / "response.json").write_text(json.dumps(resp))
    capsys.readouterr()
    assert main(args + ["--resume"]) == 0
    assert "AIQA" not in capsys.readouterr().out.replace("preflight: OK", "")    # pass findings are not printed


def test_good_cli_director_agent_bundle_root(tmp_path, assets, capsys):
    src = assets / "clip_1080x1920_30.mp4"
    args = ["director", str(src), "--seconds", "2", "--provider", "agent", "--bundle-root", str(tmp_path)]
    assert main(args) == agent.EXIT_AWAITING
    d = tmp_path / tasks.director_request_id(src, 0)
    (d / "response.json").write_text(json.dumps({"summary": "s", "shots": []}))
    capsys.readouterr()
    assert main(args + ["--resume"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["provider"] == "agent" and out["model"] == agent.MODEL_LABEL and out["data"]["summary"] == "s"


# --------------------------------------------------------------------------------------- gemini: single model + fallback
def gemini_ds(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, GEMINI_DS)
    monkeypatch.setenv("OPENGATEWAY_API_KEY", "ds-fake-key")


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "s.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x64", "-frames:v", "1", str(p)],
                   check=True)
    return p


def test_good_only_gemini_3_8_flash_is_called(tmp_path, monkeypatch):
    import re
    from video_engine import config
    assert gemini.PLANNING_MODELS == gemini.QA_MODELS == ["gemini-3.8-flash"]
    c = gemini_client({"gemini-3.8-flash": [json.dumps({"ok": 1})]})
    prov = ai.get(None, client=c)
    prov.generate_json("planning", [Text("x")], None)
    prov.generate_json("qa", [Text("x")], None)
    assert [m for m, _ in c.models.calls] == ["gemini-3.8-flash", "gemini-3.8-flash"]
    scanned = sorted((config.ENGINE_ROOT / "video_engine" / "ai").glob("*.py"))
    scanned.append(config.ENGINE_ROOT / "ai_providers.toml")
    found = set()
    for f in scanned:
        found |= set(re.findall(r"gemini-\d+(?:\.\d+)?-[a-z]+(?:-[a-z]+)*", f.read_text()))
    assert found == {"gemini-3.8-flash"}


def test_good_gemini_success_skips_fallback(tmp_path, monkeypatch):
    gemini_ds(tmp_path, monkeypatch)
    c = gemini_client({"gemini-3.8-flash": [json.dumps({"v": 1})]})
    post = FakePost({"ds-model": [ok({"v": 99})]})
    r = ai.get(None, client=c, post=post).generate_json("planning", [Text("x")], None)
    assert r.provider == "gemini" and r.model == "gemini-3.8-flash" and r.fallback is False and r.warning is None
    assert post.calls == [] and [m for m, _ in c.models.calls] == ["gemini-3.8-flash"]


def test_good_gemini_exhausted_falls_back_to_deepseek(tmp_path, monkeypatch, capsys):
    gemini_ds(tmp_path, monkeypatch)
    c = gemini_client({"gemini-3.8-flash": [APIErr(503)]})
    post = FakePost({"ds-model": [ok({"v": 2})]})
    r = ai.get(None, client=c, post=post).generate_json("planning", [Text("x")], None)
    assert r.provider == "deepseek" and r.model == "ds-model" and r.fallback is True
    assert "FALLBACK" in r.warning and "deepseek/ds-model" in r.warning
    assert [m for m, _ in c.models.calls] == ["gemini-3.8-flash"] * 4
    assert [m for m, _ in r.tried] == ["gemini-3.8-flash"] * 4 + ["ds-model"]
    assert [s for _, s in r.tried] == ["error 503"] * 4 + ["ok"]
    assert len(post.calls) == 1
    err = capsys.readouterr().err
    assert "warning: FALLBACK: gemini-3.8-flash did not answer" in err and "answer is from deepseek/ds-model" in err


def test_good_qualified_model_prefixes_only_when_missing():
    prefixed = "deepseek/deepseek-v4.1-flash-ultrafast"
    assert base.qualified_model("deepseek", prefixed) == prefixed
    assert base.qualified_model("deepseek", "ds-model") == "deepseek/ds-model"


def test_good_gemini_fallback_warning_does_not_double_prefix(tmp_path, monkeypatch, capsys):
    model = "deepseek/deepseek-v4.1-flash-ultrafast"
    write_config(tmp_path, monkeypatch, GEMINI_DS.replace("ds-model", model))
    monkeypatch.setenv("OPENGATEWAY_API_KEY", "ds-fake-key")
    c = gemini_client({"gemini-3.8-flash": [APIErr(503)]})
    post = FakePost({model: [ok({"v": 5})]})
    r = ai.get(None, client=c, post=post).generate_json("planning", [Text("x")], None)
    assert r.provider == "deepseek" and r.model == model and r.fallback
    assert model in r.warning and "answer is from " + model in r.warning
    assert "deepseek/deepseek/" not in r.warning
    assert "answer is from " + model in capsys.readouterr().err


def test_good_draft_report_names_fallback_provider_and_model(proj, tmp_path, monkeypatch):
    model = "deepseek/deepseek-v4.1-flash-ultrafast"
    write_config(tmp_path, monkeypatch, GEMINI_DS.replace("ds-model", model))
    monkeypatch.setenv("OPENGATEWAY_API_KEY", "ds-fake-key")
    c = draft_client({"gemini-3.8-flash": [APIErr(503)]})
    post = FakePost({model: [ok(good_plan())]})
    r = draft.draft(proj, provider=ai.get(None, client=c, post=post), dom=False, director=False, video_proxies=0)
    rep = json.loads((proj / "draft_report.json").read_text())
    assert r.ok and rep["provider"] == "deepseek" and rep["model"] == model and rep["fallback"] is True
    ai_meta = rep["rounds"][0]["ai"]
    assert set(ai_meta) == {"provider", "model", "retries", "fallback", "warning", "tried", "seconds", "usage"}
    assert ai_meta["provider"] == "deepseek" and ai_meta["model"] == model
    assert ai_meta["fallback"] is True and model in ai_meta["warning"]


def test_good_gemini_404_falls_back_at_once(tmp_path, monkeypatch):
    gemini_ds(tmp_path, monkeypatch)
    c = gemini_client({"gemini-3.8-flash": [APIErr(404)]})
    post = FakePost({"ds-model": [ok({"v": 3})]})
    r = ai.get(None, client=c, post=post).generate_json("planning", [Text("x")], None)
    assert r.provider == "deepseek" and r.fallback and r.tried == [("gemini-3.8-flash", "error 404"), ("ds-model", "ok")]
    assert [m for m, _ in c.models.calls] == ["gemini-3.8-flash"]


def test_good_missing_gemini_key_falls_back_to_deepseek(tmp_path, monkeypatch):
    gemini_ds(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    post = FakePost({"ds-model": [ok({"v": 4})]})
    parts = [Media(tmp_path / "a.wav", "audio", "audio/wav", "narration", transcript="halo")]
    r = ai.get(None, post=post).generate_json("planning", parts, None)
    assert r.provider == "deepseek" and r.model == "ds-model" and r.fallback
    assert "GEMINI_API_KEY is not set" in r.warning and len(post.calls) == 1


def test_good_qa_uses_gemini_3_8_flash(tmp_path, monkeypatch, png):
    gemini_ds(tmp_path, monkeypatch)
    reply = {"segments": [{"seg": 1, "overlap": False, "cut_off_text": False, "issues": []}]}
    c = gemini_client({"gemini-3.8-flash": [json.dumps(reply)]})
    post = FakePost({"ds-model": [ok({"segments": []})]})
    meta: dict = {}
    fs = tasks.qa_snapshots({1: str(png)}, ai.get(None, client=c, post=post), meta=meta)
    assert [f.level for f in fs] == ["pass"]
    assert meta == {"provider": "gemini", "model": "gemini-3.8-flash", "retries": 0, "fallback": False}
    assert [m for m, _ in c.models.calls] == ["gemini-3.8-flash"] and post.calls == []


def test_good_qa_gemini_failure_falls_back_with_warn_finding(tmp_path, monkeypatch, png):
    gemini_ds(tmp_path, monkeypatch)
    c = gemini_client({"gemini-3.8-flash": [APIErr(503)]})
    post = FakePost({"ds-model": [ok({"segments": []})]})
    meta: dict = {}
    fs = tasks.qa_snapshots({1: str(png)}, ai.get(None, client=c, post=post), meta=meta)
    assert [f.level for f in fs] == ["warn", "pass"] and "FALLBACK" in fs[0].message
    assert meta["provider"] == "deepseek" and meta["model"] == "ds-model" and meta["fallback"] is True


def test_bad_deepseek_also_fails_raises_both(tmp_path, monkeypatch):
    gemini_ds(tmp_path, monkeypatch)
    c = gemini_client({"gemini-3.8-flash": [APIErr(404)]})
    post = FakePost({"ds-model": [(400, {"error": {"message": "bad"}})]})
    with pytest.raises(EngineError) as ei:
        ai.get(None, client=c, post=post).generate_json("planning", [Text("x")], None)
    msg = str(ei.value)
    assert "gemini-3.8-flash" in msg and "deepseek" in msg and "ds-model" in msg


@pytest.mark.parametrize("bad", ["nope", "agent", "gemini"])
def test_bad_explicit_gemini_fallback_must_be_a_provider(tmp_path, monkeypatch, bad):
    text = f"""default = "gemini"
[gemini]
fallback_provider = "{bad}"
[providers.other]
base_url = "https://other.example/v1"
models = ["m"]
api_key_env = "OTHER_KEY"
"""
    write_config(tmp_path, monkeypatch, text)
    with pytest.raises(EngineError, match=rf"fallback_provider '{bad}'.*known: other"):
        ai.get(None)


def test_good_absent_gemini_fallback_key_defaults_to_deepseek(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, GEMINI_DS.replace('[gemini]\nfallback_provider = "deepseek"\n', ""))
    assert ai.get(None).fallback.name == "deepseek"
    write_config(tmp_path, monkeypatch, LLM.format(default="gemini"))
    assert ai.get(None).fallback is None


@pytest.mark.parametrize("reply", [
    '{"v": 3}',
    '```json\n{"v": 3}\n```',
    '```json\n{"v": 3}\n```\nHope this helps!',
    'Here is the JSON:\n```json\n{"v": 3}\n```',
    '{"v": 3}\n\nDone.',
    [{"type": "text", "text": '{"v": '}, {"type": "text", "text": "3}"}],     # content given as parts
])
def test_good_parse_json_text_accepts_wrapped_replies(reply):
    assert base.parse_json_text(reply) == {"v": 3}


@pytest.mark.parametrize("reply", [
    "nope", "", None,
    '{"segments": [{"start_frame": 0, "tags": [1, 2]}, {"start_frame"',      # cut off: no inner fragment is taken
    'See [the] notes: {"v": 3}',                                           # the first bracket is not the document
    '{"v": NaN}', '{"v": Infinity}',
])
def test_bad_parse_json_text_rejects_invalid_replies(reply):
    with pytest.raises(json.JSONDecodeError):
        base.parse_json_text(reply)


def test_good_openai_content_parts_are_parsed(key):
    post = FakePost({"big": [(200, {"choices": [{"message": {"content": [{"type": "text", "text": '{"v": 3}'}]}}]})]})
    r = openai_compat.OpenAICompatProvider(entry(), post).generate_json("planning", [Text("x")], None, "r")
    assert r.data == {"v": 3} and r.tried == [("big", "ok")]

