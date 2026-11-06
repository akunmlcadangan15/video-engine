"""P19 deterministic particles: schema, analytic seek, bounded count, scene wiring and a canvas DOM check."""
import json
import subprocess

import pytest
from pydantic import ValidationError

from video_engine import config, fx, hyperframes, timeline

FIREWORKS = {"kind": "fireworks", "xy": [0.5, 0.3], "at_frame": 0, "duration_s": 2.0, "seed": 3, "scale": 1.0}
FIRE = {"kind": "fire", "xy": [0.5, 0.72], "at_frame": 0, "duration_s": 2.0, "seed": 7, "scale": 1.0,
        "colours": ["#FFE07A", "#FF9A3D", "#E8452B"]}


def _scene(d):
    d["segments"][2]["gsap_scene_config"]["particles"] = [FIREWORKS, FIRE]


def test_good_particle_and_bad_values():
    assert timeline.Particle(**FIREWORKS).kind == "fireworks"
    assert timeline.Particle(**FIRE).seed == 7
    for bad in ({"kind": "smoke", "xy": [0.5, 0.5]}, {"kind": "fire", "xy": [1.2, 0.5]},
                {"kind": "fire", "xy": [0.5, 0.5], "duration_s": 0.1},
                {"kind": "fire", "xy": [0.5, 0.5], "scale": 0.1},
                {"kind": "fire", "xy": [0.5, 0.5], "colours": ["red"]}):
        with pytest.raises(ValidationError):
            timeline.Particle(**bad)


def test_good_scene_bounded_and_deterministic(project):
    path, tl = project(_scene)
    seg = tl.segments[2]
    a = hyperframes.scene_data(seg, tl, path)
    hyperframes.clear_scene_cache()
    b = hyperframes.scene_data(seg, tl, path)
    assert a["particles"] == b["particles"]
    assert all(e["count"] <= 180 for e in a["particles"])
    e = a["particles"][0]
    frames = [0, 7.5, 15, 30, 45, 59]
    first = [fx.particle_state(e, f, a["fps"], a["height"]) for f in frames]
    again = [fx.particle_state(e, f, a["fps"], a["height"]) for f in frames]
    assert first == again
    assert len(first[2]) == e["count"] and first[0] != first[2]
    fire = a["particles"][1]
    assert fx.particle_state(fire, 15, a["fps"], a["height"]) == \
        fx.particle_state(fire, 15, a["fps"], a["height"])


def test_good_canvas_draws_particles_in_the_dom(project, tmp_path):
    path, tl = project(_scene)
    build, scene = hyperframes.build_dir(tl.segments[2], tl, path, tmp_path / "hf")
    assert (build / "fx2.js").is_file() and "particles" in scene
    script = hyperframes.PROBE_JS.read_text().replace(
        "return { rects, paints, lines };",
        """const c=document.querySelector('#fx2 canvas'); let ink=0;
        if(c){const a=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          for(let i=3;i<a.length;i+=4) if(a[i]>8){ink++; if(ink>200) break;}}
        rects.push({id:'particles-test', ink:ink});
        return { rects, paints, lines };""")
    probe = tmp_path / "particle_probe.mjs"
    probe.write_text(script)
    env = config.hf_env()
    env.update(ENGINE_PUPPETEER=str(config.PUPPETEER), ENGINE_CHROME=str(config.CHROME))
    with hyperframes.serve(build) as url:
        result = subprocess.run([str(config.NODE), str(probe), url + "/index.html", "-", "0.5", "1080", "1920"],
                                env=env, text=True, capture_output=True, check=True, timeout=30)
    dom = json.loads(result.stdout)
    rect = next(r for r in dom["times"][0]["rects"] if r["id"] == "particles-test")
    assert rect["ink"] > 50 and not dom.get("errors")
