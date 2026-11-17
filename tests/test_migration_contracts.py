"""Frozen public schemas and effective profiles across the package migration."""
import hashlib
import json

from video_engine import profiles
from video_engine.profiles.schema import ProfileData
from video_engine.timeline import Timeline


def _schema_hash(model):
    return hashlib.sha256(json.dumps(model.model_json_schema(), sort_keys=True).encode()).hexdigest()


def test_engine_error_has_one_identity():
    from video_engine.core.errors import EngineError
    from video_engine.core.util import EngineError as CoreError
    from video_engine.util import EngineError as LegacyError

    assert EngineError is CoreError is LegacyError


def test_timeline_schema_contract_is_unchanged():
    assert _schema_hash(Timeline) == "c56bf64068259d9c9a776c24b58bc8aa97e36ba3b42dc2502315c771cd097d85"


def test_profile_schema_contract_is_unchanged():
    assert _schema_hash(ProfileData) == "dd33bf827db193a74831a471aa3ebee618869cab433e0a6352ccbaaaaed95f30"


def test_effective_profile_hashes_are_unchanged():
    assert profiles.load("default").blake3 == "1048453ef3a189d0b65c981fe187575821cf5520bc26298dd48282ff72e61181"
    assert profiles.load("geoverum").blake3 == "fda481d7985220ebe998c3eeb83c9f2860a611a1d9a36fb190c524aa99e0ba2c"


def test_extracted_render_helpers_participate_in_source_keys(tmp_path):
    from pathlib import Path
    from video_engine.core import image_info, vecmath
    from video_engine.core.util import code_hash
    from video_engine.render import hyperframes, native

    for renderer, helper in ((native, image_info), (hyperframes, image_info), (hyperframes, vecmath)):
        source = Path(helper.__file__).resolve()
        assert source in {Path(p).resolve() for p in renderer.CODE}
        copy = tmp_path / f"{renderer.__name__.split('.')[-1]}_{source.name}"
        copy.write_text(source.read_text())
        paths = tuple(copy if Path(p).resolve() == source else p for p in renderer.CODE)
        before = code_hash(paths)
        copy.write_text(copy.read_text() + "\nMIGRATION_CACHE_SENTINEL = 1\n")
        assert code_hash(paths) != before
