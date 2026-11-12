"""Upload a closure, run the ephemeral Modal app, and fetch outputs.

`App.run` is the same lifecycle as `modal run`: the app is not deployed. The Modal
client is imported only when a build, fetch or self-test starts.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from .modal_cli import Args, toolchain_snapshot
from .modal_closure import Closure
from .modal_transfer import (
    DiskLow,
    TransferError,
    check_run_id,
    download_manifest,
    run_remote,
    upload_plan,
)


class ModalMissing(Exception):
    """The modal client library is not installed."""


def _import_modal():
    """Import the modal client. ModalMissing when the package is not installed."""
    try:
        import modal
    except ImportError as exc:
        raise ModalMissing("the modal package is not installed (pip install modal)") from exc
    return modal


def new_run_id(digest: str) -> str:
    return check_run_id(f"{uuid.uuid4().hex[:16]}-{digest[:12]}")


class _Volume:
    """The subset of modal.Volume the launcher uses. Methods match the current SDK."""

    def __init__(self, volume, name: str):
        self.volume = volume
        self.name = name

    def exists(self, remote: str) -> bool:
        parent, _, name = remote.rpartition("/")
        if not parent:
            return False
        try:
            entries = self.volume.listdir(parent)
        except Exception:
            return False
        for entry in entries:
            raw = getattr(entry, "path", None) or str(entry)
            if str(raw).rstrip("/").split("/")[-1] == name:
                return True
        return False

    def put_file(self, local: Path, remote: str) -> None:
        try:
            with self.volume.batch_upload() as batch:
                batch.put_file(str(local), remote)
        except Exception as exc:
            text = str(exc).lower()
            if "already exists" in text or "file exists" in text:
                return
            raise

    def put_bytes(self, remote: str, data: bytes) -> None:
        with tempfile.NamedTemporaryFile(prefix="modal-put-", delete=False) as handle:
            handle.write(data)
            tmp = Path(handle.name)
        try:
            self.put_file(tmp, remote)
        finally:
            tmp.unlink(missing_ok=True)

    def read_bytes(self, remote: str) -> bytes:
        reader = getattr(self.volume, "read_file", None)
        if reader is None:
            raise TransferError("this Modal client has no Volume.read_file; upgrade the modal package")
        chunks = []
        for block in reader(remote):
            chunks.append(block if isinstance(block, (bytes, bytearray)) else bytes(block))
        return b"".join(chunks)

    def remove_tree(self, remote: str) -> None:
        remove = getattr(self.volume, "remove_file", None)
        if remove is None:
            raise TransferError("this Modal client has no Volume.remove_file; upgrade the modal package")
        try:
            remove(remote, recursive=True)
        except TypeError:
            remove(remote)
        commit = getattr(self.volume, "commit", None)
        if commit is not None:
            try:
                commit()
            except Exception:
                pass


def _open_volume(name: str):
    modal = _import_modal()
    return _Volume(modal.Volume.from_name(name, create_if_missing=True, version=2), name)


def _load_app():
    """Import modal_build after the resource env vars are set, so the function tuple matches this run."""
    import importlib.util
    import sys
    path = Path(__file__).resolve().parents[1] / "modal_build.py"
    sys.modules.pop("modal_build", None)
    spec = importlib.util.spec_from_file_location("modal_build", path)
    if spec is None or spec.loader is None:
        raise TransferError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["modal_build"] = module
    spec.loader.exec_module(module)
    return module


def _resources(args: Args) -> dict:
    return {
        "cpu": args.cpu, "memory_mb": args.memory_mb, "timeout_s": args.timeout_s, "gpu": args.gpu,
        "volume": args.volume,
    }


def _prepare_env(args: Args) -> None:
    os.environ["VIDEO_ENGINE_MODAL_CPU"] = str(args.cpu)
    os.environ["VIDEO_ENGINE_MODAL_MEMORY_MB"] = str(args.memory_mb)
    os.environ["VIDEO_ENGINE_MODAL_TIMEOUT_S"] = str(args.timeout_s)
    os.environ["VIDEO_ENGINE_MODAL_GPU"] = args.gpu
    os.environ["VIDEO_ENGINE_MODAL_VOLUME"] = args.volume
    os.environ["VIDEO_ENGINE_MODAL_DISK_MB"] = os.environ.get("VIDEO_ENGINE_MODAL_DISK_MB", "102400")


def _call(module, *payload):
    modal = _import_modal()
    with modal.enable_output():
        with module.app.run():
            return module.build_remote.remote(*payload)


def upload_closure(volume: _Volume, closure: Closure, run_id: str, resources: dict, toolchain: dict) -> dict:
    files = [item.to_dict() for item in closure.files]
    pending, present = upload_plan(files, volume.exists)
    for item in pending:
        volume.put_file(Path(item["local"]), item["remote"])
    layout = closure.layout(run_id, resources=resources, toolchain=toolchain)
    volume.put_bytes(f"{run_remote(run_id)}/layout.json", json.dumps(layout).encode("utf-8"))
    return {"uploaded": len(pending), "skipped": len(present), "run_id": run_id}


def _write_receipt(project: Path, receipt: dict) -> None:
    path = project / "out" / "modal_fetch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_build(args: Args, project: Path, closure: Closure) -> int:
    run_id = new_run_id(closure.digest)
    print(f"engine-build-modal: run {run_id} digest {closure.digest} "
          f"files {len(closure.files)} upload_bytes {closure.upload_bytes}", flush=True)
    try:
        _import_modal()
    except ModalMissing as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 127
    try:
        _prepare_env(args)
        volume = _open_volume(args.volume)
        toolchain = toolchain_snapshot()
        stats = upload_closure(volume, closure, run_id, _resources(args), toolchain)
        print(f"engine-build-modal: uploaded {stats['uploaded']} blobs, "
              f"{stats['skipped']} already on the volume", flush=True)
        module = _load_app()
        result = _call(module, run_id, False, _resources(args))
    except ModalMissing as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 127
    except DiskLow as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        print(f"engine-build-modal: run {run_id} kept on the volume; resume with --fetch {run_id}", flush=True)
        return 74
    except Exception as exc:
        print(f"engine-build-modal: {type(exc).__name__}: {exc}", flush=True)
        print(f"engine-build-modal: run {run_id} kept on the volume; resume with --fetch {run_id}", flush=True)
        return 1
    code = int(result.get("returncode", 1)) if isinstance(result, dict) else 1
    try:
        fetch_stats = _download(volume, run_id, project, args.min_free_bytes)
    except DiskLow as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        print(f"engine-build-modal: run {run_id} kept; resume with --fetch {run_id}", flush=True)
        return 74
    except TransferError as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        print(f"engine-build-modal: run {run_id} kept; resume with --fetch {run_id}", flush=True)
        return 1
    removed = False
    if not args.keep_volume:
        volume.remove_tree(run_remote(run_id))
        removed = True
        print(f"engine-build-modal: removed volume run {run_id}", flush=True)
    else:
        print(f"engine-build-modal: kept volume run {run_id}", flush=True)
    _write_receipt(project, {
        "run_id": run_id, "digest": closure.digest, "returncode": code,
        "upload": stats, "fetch": fetch_stats, "volume_run_removed": removed,
        "remote": result.get("remote") if isinstance(result, dict) else {},
    })
    return code


def resume_fetch(args: Args, project: Path, run_id: str) -> int:
    try:
        run_id = check_run_id(run_id)
    except TransferError as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 2
    try:
        _import_modal()
    except ModalMissing as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 127
    try:
        volume = _open_volume(args.volume)
        stats = _download(volume, run_id, project, args.min_free_bytes)
    except DiskLow as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 74
    except TransferError as exc:
        print(f"engine-build-modal: {exc}", flush=True)
        return 1
    removed = False
    if not args.keep_volume:
        volume.remove_tree(run_remote(run_id))
        removed = True
        print(f"engine-build-modal: removed volume run {run_id}", flush=True)
    _write_receipt(project, {"run_id": run_id, "fetch": stats, "volume_run_removed": removed, "resumed": True})
    return 0


def run_selftest(args: Args) -> dict:
    _import_modal()
    _prepare_env(args)
    module = _load_app()
    result = _call(module, "", True, _resources(args))
    if not isinstance(result, dict):
        raise TransferError("self-test returned no doctor payload")
    return result


def _download(volume: _Volume, run_id: str, project: Path, min_free: int) -> dict:
    remote = f"{run_remote(run_id)}/fetch/manifest.json"
    try:
        raw = volume.read_bytes(remote)
    except Exception as exc:
        raise TransferError(f"fetch manifest for {run_id} is not on the volume ({exc})") from exc
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TransferError(f"fetch manifest for {run_id} is not JSON ({exc})") from exc

    def reader(path: str) -> bytes:
        return volume.read_bytes(path)

    stats = download_manifest(manifest, reader, project, run_id=run_id, min_free=min_free)
    print(f"engine-build-modal: fetched {len(stats['files'])} files into {project}", flush=True)
    return stats
