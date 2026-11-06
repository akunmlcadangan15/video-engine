"""Cloud container runner for video-engine on Modal.com.

Usage:
  modal setup
  modal run modal_render.py --project examples/minimal
"""
import os
import subprocess
import modal

app = modal.App("video-engine")

image = (
    modal.Image.from_dockerfile("Dockerfile.modal")
    .add_local_dir(".", "/src")
)

volume = modal.Volume.from_name("video-engine-cache", create_if_missing=True)


@app.function(image=image, cpu=16, memory=16384, gpu="L4", timeout=1200, volumes={"/cache": volume})
def build_project(project: str) -> int:
    env = {**os.environ, "VIDEO_ENGINE_CACHE": "/cache"}
    r = subprocess.run(["engine", "build", project], cwd="/src", env=env, capture_output=True, text=True)
    print(r.stdout[-2000:])
    print(r.stderr[-2000:])
    volume.commit()
    return r.returncode


@app.local_entrypoint()
def run(project: str = "examples/minimal") -> None:
    build_project.remote(project)
