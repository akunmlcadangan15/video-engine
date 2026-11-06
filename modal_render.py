"""Modal renderer untuk Hermes video-engine: render 1:1 di Modal.com (kredit $250).
Cara pakai:
  modal setup (sekali, login pakai token Modal)
  modal run modal_render.py --project examples/minimal
Sweet spot: cpu=16, memory=16384, gpu=L4 (untuk encode NVENC draft) atau gpu=None untuk x264 rilis.
HyperFrames tetap CPU (SwiftShader), GPU hanya untuk encode.
"""
import modal

app = modal.App("hermes-video-engine")

image = (
    modal.Image.from_dockerfile("Dockerfile.modal")
    .add_local_dir(".", "/src")
    .run_commands("pip install -e \".[test]\" && python -m maturin develop --release || true")
)

volume = modal.Volume.from_name("hermes-cache", create_if_missing=True)

@app.function(image=image, cpu=16, memory=16384, gpu="L4", timeout=1200, volumes={"/cache": volume})
def build_project(project: str):
    import subprocess, pathlib
    r = subprocess.run(["engine", "build", project], cwd="/src", capture_output=True, text=True)
    print(r.stdout[-2000:])
    print(r.stderr[-2000:])
    return r.returncode

@app.local_entrypoint()
def run(project: str = "examples/minimal"):
    build_project.remote(project)
