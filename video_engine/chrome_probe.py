"""Shared-library and headless-launch checks for chrome-headless-shell.

The Modal image is Ubuntu 24.04. chrome-headless-shell is a dynamically linked ELF and
does not start until the libraries below are installed. These helpers do not import the
native extension, so the parsers can be tested on their own.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Ubuntu 24.04 package names (t64 where the archive renamed them). deploy/modal.Dockerfile installs this set.
CHROME_RUNTIME_PACKAGES = (
    "libnss3",
    "libnspr4",
    "libglib2.0-0t64",
    "libatk1.0-0t64",
    "libatk-bridge2.0-0t64",
    "libatspi2.0-0t64",
    "libcups2t64",
    "libdbus-1-3",
    "libdrm2",
    "libgbm1",
    "libxkbcommon0",
    "libx11-6",
    "libxcb1",
    "libxext6",
    "libxcomposite1",
    "libxdamage1",
    "libxfixes3",
    "libxrandr2",
    "libpango-1.0-0",
    "libcairo2",
    "libasound2t64",
    "fonts-liberation",
    "fontconfig",
)

# Sonames a headless-shell ldd reports when those packages are absent.
CHROME_LINUX_SONAMES = (
    "libglib-2.0.so.0",
    "libgobject-2.0.so.0",
    "libgio-2.0.so.0",
    "libnspr4.so",
    "libnss3.so",
    "libnssutil3.so",
    "libatk-1.0.so.0",
    "libatk-bridge-2.0.so.0",
    "libatspi.so.0",
    "libdbus-1.so.3",
    "libX11.so.6",
    "libXcomposite.so.1",
    "libXdamage.so.1",
    "libXext.so.6",
    "libXfixes.so.3",
    "libXrandr.so.2",
    "libgbm.so.1",
    "libxcb.so.1",
    "libxkbcommon.so.0",
    "libasound.so.2",
)


def parse_ldd(text: str) -> list[str]:
    """Sonames in an ldd transcript that are 'not found', in order, without duplicates."""
    missing: list[str] = []
    for line in (text or "").splitlines():
        if "not found" not in line:
            continue
        name = line.strip().split()[0] if line.strip() else ""
        if name and name not in missing:
            missing.append(name)
    return missing


def is_elf(path: Path) -> bool:
    try:
        with Path(path).open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def ldd_missing(binary: Path, *, run=None) -> list[str] | None:
    """Sonames ldd cannot resolve. None when `binary` is not a Linux ELF (macOS Chrome, or no file).

    An empty list means ldd reported no missing libraries, or ldd itself is not installed.
    `run(cmd) -> (returncode, stdout, stderr)` replaces the ldd process in tests.
    """
    path = Path(binary)
    if not path.is_file() or not is_elf(path):
        return None
    if run is None:
        ldd = shutil.which("ldd")
        if ldd is None:
            return []

        def run(cmd):
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            return proc.returncode, proc.stdout or "", proc.stderr or ""

        command = [ldd, str(path)]
    else:
        command = ["ldd", str(path)]
    _code, stdout, _err = run(command)
    return parse_ldd(stdout)


def chrome_libs_status(binary: Path | None) -> tuple[bool, str, bool]:
    """(ok, got, warn) for a doctor line. A missing Linux library fails. No binary, or not an ELF, does not."""
    if binary is None or not Path(binary).is_file():
        return True, "skipped (no binary)", True
    missing = ldd_missing(Path(binary))
    if missing is None:
        return True, "not a Linux ELF", False
    if missing:
        return False, "missing " + ", ".join(missing), False
    return True, "present", False


def chrome_launch_command(binary: Path) -> list[str]:
    """One-shot headless launch. --no-sandbox is required in the container; --dump-dom exits after about:blank."""
    return [
        str(binary),
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--dump-dom",
        "about:blank",
    ]


def chrome_launch_result(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    if returncode == 0:
        return True, "about:blank"
    tail = (stderr or stdout or "").strip().splitlines()
    line = tail[-1][:400] if tail else ""
    return False, f"exit {returncode}: {line}".rstrip()


def launch_chrome(binary: Path, *, run=None, timeout: int = 30) -> dict:
    """Run chrome-headless-shell on about:blank. `run(cmd) -> (code, stdout, stderr)` in tests.

    Returns {"ok": bool, "detail": str}. A missing shared library shows up as a non-zero exit.
    """
    path = Path(binary)
    if not path.is_file():
        return {"ok": False, "detail": f"missing {path}"}
    command = chrome_launch_command(path)
    try:
        if run is None:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            code, stdout, stderr = proc.returncode, proc.stdout or "", proc.stderr or ""
        else:
            code, stdout, stderr = run(command)
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": f"timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)}
    ok, detail = chrome_launch_result(code, stdout, stderr)
    return {"ok": ok, "detail": detail}


def chrome_launch_problem(remote: dict) -> str | None:
    """None when the remote self-test launched Chrome. Otherwise a line for the mismatch list."""
    launch = remote.get("chrome_launch") if isinstance(remote, dict) else None
    if isinstance(launch, dict) and launch.get("ok"):
        return None
    if isinstance(launch, dict):
        detail = str(launch.get("detail") or "failed")
    else:
        detail = "did not run"
    return f"chrome launch: {detail}"
