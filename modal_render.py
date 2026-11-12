"""CPU-only stub kept so `modal run modal_render.py` does not request a GPU.

Earlier revisions of this module requested an L4 GPU and 16 CPUs and copied only the
repository into the image, so a project that lives outside the checkout never ran and
its outputs disappeared with the container.

Final builds use scripts/engine-build-modal. That command uploads the project closure,
runs CPU-only unless --gpu L4 is set, and fetches out/ back. This module does not build.
"""
from __future__ import annotations

import modal

app = modal.App("video-engine")


@app.local_entrypoint()
def run(project: str = "examples/minimal") -> None:
    raise SystemExit(
        "modal_render.py does not start a build and does not request a GPU. "
        f"Run: scripts/engine-build-modal {project}"
    )
