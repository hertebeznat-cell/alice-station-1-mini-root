"""macOS image builder using Docker Desktop; USB remains on the host."""

from __future__ import annotations

import subprocess
import shutil
from pathlib import Path
from .environment import run_capture

IMAGE = "station-mini-root-builder:0.3.12"


def docker_binary():
    candidates = [
        shutil.which("docker"),
        str(Path.home() / ".docker/bin/docker"),
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), "")


def ready():
    docker = docker_binary()
    if not docker:
        return False
    try:
        return run_capture([docker, "image", "inspect", IMAGE], timeout=20)[0] == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def setup(resources, run_process):
    docker = docker_binary()
    if not docker:
        raise RuntimeError("Install and start Docker Desktop before setting up the macOS builder.")
    run_process(
        [
            docker,
            "build",
            "--platform",
            "linux/amd64",
            "-t",
            IMAGE,
            "-f",
            str(Path(resources) / "scripts/Dockerfile.mac-builder"),
            str(resources),
        ]
    )
    if not ready():
        raise RuntimeError("Docker builder image is not available. Start Docker Desktop and retry setup.")


def command(resources, source, public, output):
    docker = docker_binary()
    if not docker or not ready():
        raise RuntimeError("Docker builder is unavailable. Start Docker Desktop and install dependencies first.")
    source, public, output = (Path(p).resolve() for p in (source, public, output))
    work = output.parent
    if source.parent != work or public.parent != work / "keys":
        raise ValueError("macOS builder requires the backup and ADB key in the workspace")
    # Docker -v uses colon as a delimiter. Refuse ambiguous paths before writing.
    for path in (work, Path(resources).resolve()):
        if ":" in str(path):
            raise ValueError("Docker cannot mount paths containing a colon")
    return [
        docker,
        "run",
        "--rm",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "-v",
        str(Path(resources).resolve()) + ":/src:ro",
        "-v",
        str(work) + ":/data",
        IMAGE,
        "env",
        "STATION_ROOT_PYTHON=/opt/mini/bin/python",
        "bash",
        "scripts/build_image.sh",
        "/data/" + source.name,
        "/data/keys/" + public.name,
        "/data/" + output.name,
    ]
