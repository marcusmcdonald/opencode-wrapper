#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _find_repo_or_data_root() -> Path:
    env_dir = os.environ.get("OPENCODE_WRAPPER_ROOT")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    repo_candidate = Path(__file__).resolve().parents[2]
    if (repo_candidate / "pyproject.toml").is_file():
        return repo_candidate

    return Path.home() / ".local" / "share" / "opencode-wrapper"


def _find_default_dockerfile() -> Path:
    pkg_df = Path(__file__).resolve().parent / "Dockerfile"
    if pkg_df.is_file():
        return pkg_df

    repo_root = _find_repo_or_data_root()
    repo_df = repo_root / "Dockerfile"
    if repo_df.is_file():
        return repo_df

    cwd_df = Path.cwd() / "Dockerfile"
    if cwd_df.is_file():
        return cwd_df

    return pkg_df


def _find_default_sif_path() -> Path:
    repo_root = _find_repo_or_data_root()
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "opencode.sif"
    return Path.cwd() / "opencode.sif"


DEFAULT_DOCKERFILE = _find_default_dockerfile()
DEFAULT_SIF_PATH = _find_default_sif_path()
DEFAULT_PODMAN_TAG = "opencode:latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build OpenCode container image using either Apptainer or Podman from a single Dockerfile."
    )
    parser.add_argument(
        "-e",
        "--engine",
        choices=["auto", "apptainer", "podman"],
        default="auto",
        help="Build engine to use. Default: auto-detect",
    )
    parser.add_argument(
        "-f",
        "--file",
        default=str(DEFAULT_DOCKERFILE),
        help=f"Path to Dockerfile. Default: {DEFAULT_DOCKERFILE.name}",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help=(
            "Output target. For Apptainer: path to output .sif (default: opencode.sif). "
            "For Podman: image tag (default: opencode:latest)."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not use cache when building.",
    )
    return parser.parse_args()


def detect_engine(requested: str) -> str:
    if requested in ("apptainer", "podman"):
        if shutil.which(requested) is None:
            sys.exit(f"Error: Requested engine '{requested}' is not installed or not in PATH.")
        return requested

    if shutil.which("podman"):
        return "podman"
    if shutil.which("apptainer"):
        return "apptainer"

    sys.exit("Error: Neither 'podman' nor 'apptainer' was found in PATH.")


def build_with_podman(dockerfile: Path, tag: str, no_cache: bool) -> int:
    cmd = ["podman", "build", "-t", tag, "-f", str(dockerfile), str(dockerfile.parent)]
    if no_cache:
        cmd.append("--no-cache")

    print(f"Building Podman image '{tag}' from {dockerfile}...")
    return subprocess.run(cmd).returncode


def build_with_apptainer(dockerfile: Path, output_sif: Path, no_cache: bool) -> int:
    try:
        from spython.main.parse.parsers import DockerParser
        from spython.main.parse.writers import SingularityWriter
    except ImportError:
        sys.exit(
            "Error: 'spython' is required for Apptainer builds from Dockerfile.\n"
            "Install it via: uv pip install spython (or pip install spython)"
        )

    print(f"Converting {dockerfile.name} to temporary Apptainer recipe...")
    parser = DockerParser(str(dockerfile))
    writer = SingularityWriter(parser.recipe)
    def_content = writer.convert()

    with tempfile.NamedTemporaryFile("w", suffix=".def", delete=False) as tmp_def:
        tmp_def.write(def_content)
        tmp_def_path = Path(tmp_def.name)

    cmd = ["apptainer", "build"]
    if no_cache:
        cmd.append("--disable-cache")
    cmd.extend([str(output_sif), str(tmp_def_path)])

    print(f"Building Apptainer SIF '{output_sif}'...")
    try:
        return subprocess.run(cmd).returncode
    finally:
        tmp_def_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    dockerfile = Path(args.file).resolve()

    if not dockerfile.is_file():
        sys.exit(f"Error: Dockerfile not found at {dockerfile}")

    engine = detect_engine(args.engine)

    if engine == "podman":
        tag = args.output if args.output else DEFAULT_PODMAN_TAG
        return build_with_podman(dockerfile, tag, args.no_cache)

    output_sif = Path(args.output).resolve() if args.output else DEFAULT_SIF_PATH
    return build_with_apptainer(dockerfile, output_sif, args.no_cache)


if __name__ == "__main__":
    sys.exit(main())