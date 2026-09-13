#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from enum import IntEnum
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

WRAPPER_VERSION = "1.1.0"


def _find_repo_or_data_root() -> Path:
    env_dir = os.environ.get("OPENCODE_WRAPPER_ROOT")
    if env_dir:
        return Path(env_dir).expanduser().resolve()

    # If installed in editable mode or running from git checkout
    repo_candidate = Path(__file__).resolve().parents[2]
    if (repo_candidate / "pyproject.toml").is_file():
        return repo_candidate

    return Path.home() / ".local" / "share" / "opencode-wrapper"


def _find_default_apptainer_image() -> Path:
    cwd_sif = Path.cwd() / "opencode.sif"
    if cwd_sif.is_file():
        return cwd_sif

    repo_root = _find_repo_or_data_root()
    repo_sif = repo_root / "opencode.sif"
    if repo_sif.is_file():
        return repo_sif

    pkg_sif = Path(__file__).resolve().parent / "opencode.sif"
    if pkg_sif.is_file():
        return pkg_sif

    return repo_sif


_ROOT_DIR = _find_repo_or_data_root()
DEFAULT_APPTAINER_IMAGE = _find_default_apptainer_image()
DEFAULT_PODMAN_IMAGE = "opencode:latest"

DEFAULT_HOST_CONFIG_DIR = _ROOT_DIR / "config" / "opencode"
DEFAULT_HOST_DATA_DIR = _ROOT_DIR / "config" / ".local" / "share" / "opencode"

CONTAINER_PROJECT_DIR = "/workspace/project"
CONTAINER_CONFIG_DIR = "/workspace/opencode-config"
CONTAINER_DATA_HOME = "/workspace/opencode-data"


class ExitCode(IntEnum):
    SUCCESS = 0
    GENERAL_ERROR = 1
    INTERRUPTED = 130


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OpenCode inside an isolated Apptainer or Podman container.",
        usage="%(prog)s [options] [project_dir] [opencode_args ...]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )

    parser.add_argument(
        "-e",
        "--engine",
        choices=["auto", "apptainer", "podman"],
        default="auto",
        help="Container runtime engine to use ('auto', 'apptainer', 'podman'). Default: auto",
    )

    parser.add_argument(
        "--image",
        default=None,
        help=(
            "Path to SIF file (Apptainer) or image tag/archive (Podman). "
            f"Defaults: '{DEFAULT_APPTAINER_IMAGE.name}' (Apptainer), "
            f"'{DEFAULT_PODMAN_IMAGE}' (Podman)."
        ),
    )

    parser.add_argument(
        "--sandbox-config-dir",
        default=str(DEFAULT_HOST_CONFIG_DIR),
        help=(
            "Host directory used to persist OpenCode configuration. "
            f"Default: {DEFAULT_HOST_CONFIG_DIR}"
        ),
    )

    parser.add_argument(
        "--sandbox-data-dir",
        default=str(DEFAULT_HOST_DATA_DIR),
        help=(
            "Host directory used to persist OpenCode data and authentication. "
            f"Default: {DEFAULT_HOST_DATA_DIR}"
        ),
    )

    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help=(
            "Path to a .env file or directory containing a .env file. "
            "Defaults to checking the wrapper root then the project directory."
        ),
    )

    parser.add_argument(
        "--install-kai",
        action="store_true",
        help="Download and install Kai multi-agent orchestration into the configuration directory.",
    )

    config_group = parser.add_mutually_exclusive_group()
    config_group.add_argument(
        "--copy-config",
        metavar="SOURCE",
        help=(
            "Copy an existing OpenCode configuration file or directory into "
            "the sandbox configuration directory before launching."
        ),
    )
    config_group.add_argument(
        "--reset-config",
        action="store_true",
        help="Delete the existing sandbox configuration before launching.",
    )

    data_group = parser.add_mutually_exclusive_group()
    data_group.add_argument(
        "--copy-data",
        metavar="SOURCE",
        help=(
            "Copy an existing OpenCode data directory into the sandbox data "
            "directory before launching. This can include auth.json."
        ),
    )
    data_group.add_argument(
        "--reset-data",
        action="store_true",
        help="Delete the existing sandbox data directory before launching.",
    )

    parser.add_argument(
        "--debug-container",
        action="store_true",
        help="Print container paths, environment variables, and config contents.",
    )

    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Show wrapper version and opencode version.",
    )

    parser.add_argument(
        "-h",
        "--help",
        action="store_true",
        help="Show this help message and exit.",
    )

    return parser


def _strip_jsonc_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False

    while i < n:
        c = text[i]

        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue

        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in ("\r", "\n"):
                i += 1
            continue

        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue

        out.append(c)
        i += 1

    stripped = "".join(out)
    return re.sub(r",\s*([\]}])", r"\1", stripped)


def configure_kai_agent(config_path: Path) -> None:
    data: dict[str, Any] = {}
    if config_path.is_file():
        try:
            content = config_path.read_text(encoding="utf-8")
            parsed = json.loads(_strip_jsonc_comments(content))
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, OSError) as error:
            print(
                f"Warning: Could not parse {config_path.name} ({error}). Initializing new configuration.",
                file=sys.stderr,
            )
            data = {}

    data["default_agent"] = "kai"

    agents = data.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        data["agents"] = agents

    for agent_name in ("build", "plan"):
        agent_conf = agents.get(agent_name)
        if not isinstance(agent_conf, dict):
            agent_conf = {}
            agents[agent_name] = agent_conf
        agent_conf["disabled"] = True

    try:
        config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(
            f"Configured Kai as default agent and disabled build/plan in {config_path.name}"
        )
    except OSError as error:
        print(
            f"Warning: Failed to write Kai configuration to {config_path}: {error}",
            file=sys.stderr,
        )


def install_kai(target_config_dir: Path) -> None:
    installer_url = "https://kai.21no.de/scripts/installer.sh"
    print(f"Downloading and installing Kai into {target_config_dir}...")

    had_json = (target_config_dir / "opencode.json").is_file()
    had_jsonc = (target_config_dir / "opencode.jsonc").is_file()

    cmd = [
        "bash",
        "-c",
        'curl -fsSL "$1" | bash -s -- latest --yes --config-dir "$2"',
        "_",
        installer_url,
        str(target_config_dir),
    ]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print("Warning: Kai installer failed or curl was unavailable.", file=sys.stderr)

    if not had_json and had_jsonc:
        created_json = target_config_dir / "opencode.json"
        if created_json.is_file():
            created_json.unlink(missing_ok=True)
        target_config = target_config_dir / "opencode.jsonc"
    elif had_json:
        target_config = target_config_dir / "opencode.json"
    elif had_jsonc:
        target_config = target_config_dir / "opencode.jsonc"
    else:
        target_config = target_config_dir / "opencode.json"

    configure_kai_agent(target_config)


def detect_engine(engine_arg: str, image_arg: str | None) -> str:
    if engine_arg in ("apptainer", "podman"):
        return engine_arg

    if image_arg:
        if image_arg.endswith((".sif", ".simg")) or Path(image_arg).is_file():
            return "apptainer"
        if (":" in image_arg or "/" in image_arg) and shutil.which("podman"):
            return "podman"

    if DEFAULT_APPTAINER_IMAGE.is_file() and shutil.which("apptainer"):
        return "apptainer"

    if shutil.which("podman"):
        return "podman"

    if shutil.which("apptainer"):
        return "apptainer"

    return "apptainer"


def resolve_image(image_arg: str | None, engine: str) -> str:
    if image_arg:
        if engine == "apptainer":
            return str(resolve_path(image_arg))
        return image_arg

    if engine == "apptainer":
        return str(DEFAULT_APPTAINER_IMAGE)
    return DEFAULT_PODMAN_IMAGE


def _run_container_probe(engine: str, image: str, probe_args: list[str]) -> None:
    if shutil.which(engine) is None:
        return

    if engine == "apptainer":
        if not Path(image).is_file():
            return
        cmd = [
            "apptainer",
            "exec",
            "--containall",
            "--cleanenv",
            "--workdir",
            "/tmp",
            "--pwd",
            CONTAINER_PROJECT_DIR,
            "--bind",
            f"{Path.cwd()}:{CONTAINER_PROJECT_DIR}:rw",
        ]
        if DEFAULT_HOST_CONFIG_DIR.exists():
            cmd.extend(
                ["--bind", f"{DEFAULT_HOST_CONFIG_DIR}:{CONTAINER_CONFIG_DIR}:rw"]
            )
        if DEFAULT_HOST_DATA_DIR.exists():
            cmd.extend(
                ["--bind", f"{DEFAULT_HOST_DATA_DIR}:{CONTAINER_DATA_HOME}/opencode:rw"]
            )
        cmd.extend([image, "opencode", *probe_args])
    else:
        cmd = [
            "podman",
            "run",
            "--rm",
            "--entrypoint",
            "",
            "--workdir",
            CONTAINER_PROJECT_DIR,
            "-v",
            f"{Path.cwd()}:{CONTAINER_PROJECT_DIR}:rw",
        ]
        if DEFAULT_HOST_CONFIG_DIR.exists():
            cmd.extend(["-v", f"{DEFAULT_HOST_CONFIG_DIR}:{CONTAINER_CONFIG_DIR}:rw"])
        if DEFAULT_HOST_DATA_DIR.exists():
            cmd.extend(
                ["-v", f"{DEFAULT_HOST_DATA_DIR}:{CONTAINER_DATA_HOME}/opencode:rw"]
            )
        cmd.extend([image, "opencode", *probe_args])

    try:
        subprocess.run(cmd, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _print_help_and_exit(engine: str, image: str) -> None:
    parser = _create_parser()
    parser.print_help()
    print()
    print("==========================================================")
    print(f"OpenCode Help (from {engine} container)")
    print("==========================================================")
    _run_container_probe(engine, image, ["--help"])
    sys.exit(0)


def _print_version_and_exit(engine: str, image: str) -> None:
    print(f"opencode-wrapper version {WRAPPER_VERSION}")
    print("==========================================================")
    print(f"OpenCode Version (from {engine} container)")
    print("==========================================================")
    _run_container_probe(engine, image, ["--version"])
    sys.exit(0)


def _looks_like_path(arg: str) -> bool:
    return (
        "/" in arg
        or "\\" in arg
        or arg in (".", "..")
        or arg.startswith(("~", "./", "../"))
    )


def parse_args() -> argparse.Namespace:
    parser = _create_parser()
    args, unknown = parser.parse_known_args(sys.argv[1:])

    args.engine = detect_engine(args.engine, args.image)
    args.image = resolve_image(args.image, args.engine)

    if args.help:
        _print_help_and_exit(args.engine, args.image)
    if args.version:
        _print_version_and_exit(args.engine, args.image)

    if unknown and (
        Path(unknown[0]).expanduser().is_dir() or _looks_like_path(unknown[0])
    ):
        potential_path = Path(unknown[0]).expanduser().resolve()
        if not potential_path.exists():
            parser.error(f"Project directory does not exist: {potential_path}")
        if not potential_path.is_dir():
            parser.error(f"Project path is not a directory: {potential_path}")
        args.project_dir = str(potential_path)
        args.opencode_args = unknown[1:]
    else:
        args.opencode_args = unknown
        args.project_dir = str(Path.cwd())

    return args


def resolve_path(path_str: str) -> Path:
    return Path(path_str).expanduser().resolve()


def resolve_env_file(
    env_file_arg: str | None,
    project_dir: Path,
    root_dir: Path,
) -> Path | None:
    if env_file_arg is not None:
        trimmed = env_file_arg.strip()
        if not trimmed:
            raise ValueError("Environment file path cannot be empty.")

        path = resolve_path(trimmed)
        if path.is_dir():
            candidate = path / ".env"
            if not candidate.is_file():
                raise FileNotFoundError(
                    f"Specified environment file does not exist: {candidate}"
                )
            return candidate

        if trimmed.endswith(("/", "\\")):
            raise FileNotFoundError(
                f"Specified environment directory does not exist: {path}"
            )

        if not path.is_file():
            raise FileNotFoundError(
                f"Specified environment file does not exist: {path}"
            )
        return path

    root_env = root_dir / ".env"
    if root_env.is_file():
        return root_env

    project_env = project_dir / ".env"
    if project_env.is_file():
        return project_env

    return None


def reset_directory(path: Path) -> None:
    if path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    path.mkdir(parents=True, exist_ok=True)


def ensure_directory(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise RuntimeError(f"Expected a directory but found a file: {path}")

    path.mkdir(parents=True, exist_ok=True)


def copy_into_directory(source_path_str: str, destination: Path) -> None:
    source = resolve_path(source_path_str)

    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")

    destination.mkdir(parents=True, exist_ok=True)

    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination / source.name)


def find_config_file(config_dir: Path) -> Path | None:
    preferred_names = (
        "opencode.json",
        "opencode.jsonc",
    )

    for name in preferred_names:
        candidate = config_dir / name
        if candidate.is_file():
            return candidate

    return None


PROTECTED_CONTAINER_VARS = {
    "OPENCODE_CONFIG_DIR",
    "OPENCODE_CONFIG",
    "XDG_DATA_HOME",
}


def build_container_environment(
    config_file: Path | None,
    env_file: Path | None = None,
) -> dict[str, str]:
    environment: dict[str, str] = {
        "OPENCODE_CONFIG_DIR": CONTAINER_CONFIG_DIR,
        "XDG_DATA_HOME": CONTAINER_DATA_HOME,
    }

    gui_vars = (
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    )
    for var in gui_vars:
        val = os.getenv(var)
        if val is not None:
            environment[var] = val

    if config_file is not None:
        environment["OPENCODE_CONFIG"] = f"{CONTAINER_CONFIG_DIR}/{config_file.name}"

    passthrough_variables = (
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    )

    for variable_name in passthrough_variables:
        value = os.environ.get(variable_name)
        if value:
            environment[variable_name] = value

    if env_file is not None and env_file.is_file():
        try:
            file_vars = dotenv_values(env_file)
        except (OSError, UnicodeDecodeError) as error:
            print(
                f"Warning: Failed to parse environment file {env_file}: {error}",
                file=sys.stderr,
            )
            file_vars = {}

        for key, file_val in file_vars.items():
            if not key or key in PROTECTED_CONTAINER_VARS:
                continue
            # Host shell environment takes precedence over .env file
            val = os.environ.get(key)
            if val is None and file_val is not None:
                val = file_val
            if val is not None:
                environment[key] = val

    return environment


def build_debug_command() -> list[str]:
    return [
        "bash",
        "-c",
        """
set -eu

echo "=========================================================="
echo "Container identity"
echo "=========================================================="
id
echo

echo "=========================================================="
echo "Environment"
echo "=========================================================="
printf 'HOME=%s\\n' "${HOME:-unset}"
printf 'PWD=%s\\n' "${PWD:-unset}"
printf 'XDG_CONFIG_HOME=%s\\n' "${XDG_CONFIG_HOME:-unset}"
printf 'XDG_DATA_HOME=%s\\n' "${XDG_DATA_HOME:-unset}"
printf 'OPENCODE_CONFIG=%s\\n' "${OPENCODE_CONFIG:-unset}"
printf 'OPENCODE_CONFIG_DIR=%s\\n' "${OPENCODE_CONFIG_DIR:-unset}"
echo

echo "=========================================================="
echo "Project directory"
echo "=========================================================="
ls -la /workspace/project
echo

echo "=========================================================="
echo "Configuration directory"
echo "=========================================================="
ls -la /workspace/opencode-config
echo

if [ -n "${OPENCODE_CONFIG:-}" ] && [ -f "$OPENCODE_CONFIG" ]; then
    echo "=========================================================="
    echo "Primary configuration"
    echo "=========================================================="
    cat "$OPENCODE_CONFIG"
    echo
fi

echo "=========================================================="
echo "Data directory"
echo "=========================================================="
find /workspace/opencode-data -maxdepth 3 -type f -print 2>/dev/null || true
echo

echo "=========================================================="
echo "OpenCode executable"
echo "=========================================================="
command -v opencode
opencode --version || true
echo

echo "=========================================================="
echo "Resolved OpenCode configuration"
echo "=========================================================="
opencode debug config || true
""",
    ]


def build_apptainer_command(
    image_path: Path,
    project_dir: Path,
    config_dir: Path,
    data_dir: Path,
    opencode_args: list[str],
    debug_container: bool,
) -> list[str]:
    command = [
        "apptainer",
        "exec",
        "--containall",
        "--cleanenv",
        "--workdir",
        "/tmp",
        "--pwd",
        CONTAINER_PROJECT_DIR,
    ]

    xdg_runtime = os.getenv("XDG_RUNTIME_DIR")
    if xdg_runtime and Path(xdg_runtime).exists():
        command.extend(["--bind", f"{xdg_runtime}:{xdg_runtime}:rw"])

    x11_dir = Path("/tmp/.X11-unix")
    if x11_dir.exists():
        command.extend(["--bind", f"{x11_dir}:{x11_dir}:rw"])

    command.extend(
        [
            "--bind",
            f"{project_dir}:{CONTAINER_PROJECT_DIR}:rw",
            "--bind",
            f"{config_dir}:{CONTAINER_CONFIG_DIR}:rw",
            "--bind",
            f"{data_dir}:{CONTAINER_DATA_HOME}/opencode:rw",
        ]
    )

    command.append(str(image_path))

    if debug_container:
        command.extend(build_debug_command())
    else:
        command.append("opencode")
        command.extend(opencode_args)

    return command


def build_podman_command(
    image: str,
    project_dir: Path,
    config_dir: Path,
    data_dir: Path,
    container_env: dict[str, str],
    opencode_args: list[str],
    debug_container: bool,
) -> list[str]:
    command = [
        "podman",
        "run",
        "--rm",
        "--interactive",
    ]

    if sys.stdin.isatty():
        command.append("--tty")

    command.extend(
        [
            "--userns=keep-id",
            "--workdir",
            CONTAINER_PROJECT_DIR,
            "-v",
            f"{project_dir}:{CONTAINER_PROJECT_DIR}:rw",
            "-v",
            f"{config_dir}:{CONTAINER_CONFIG_DIR}:rw",
            "-v",
            f"{data_dir}:{CONTAINER_DATA_HOME}/opencode:rw",
        ]
    )

    xdg_runtime = os.getenv("XDG_RUNTIME_DIR")
    if xdg_runtime and Path(xdg_runtime).exists():
        command.extend(["-v", f"{xdg_runtime}:{xdg_runtime}:rw"])

    x11_dir = Path("/tmp/.X11-unix")
    if x11_dir.exists():
        command.extend(["-v", f"{x11_dir}:{x11_dir}:rw"])

    for name, value in container_env.items():
        if name in PROTECTED_CONTAINER_VARS:
            command.extend(["--env", f"{name}={value}"])
        else:
            command.extend(["--env", name])

    command.extend(["--entrypoint", ""])
    command.append(image)

    if debug_container:
        command.extend(build_debug_command())
    else:
        command.append("opencode")
        command.extend(opencode_args)

    return command


def main() -> int:
    args = parse_args()

    project_dir = resolve_path(args.project_dir)
    config_dir = resolve_path(args.sandbox_config_dir)
    data_dir = resolve_path(args.sandbox_data_dir)

    if not project_dir.is_dir():
        print(
            f"Error: Project directory does not exist: {project_dir}",
            file=sys.stderr,
        )
        return ExitCode.GENERAL_ERROR

    try:
        env_file = resolve_env_file(
            args.env_file,
            project_dir=project_dir,
            root_dir=_ROOT_DIR,
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return ExitCode.GENERAL_ERROR

    if shutil.which(args.engine) is None:
        print(
            f"Error: The '{args.engine}' executable was not found in PATH.",
            file=sys.stderr,
        )
        return ExitCode.GENERAL_ERROR

    if args.engine == "apptainer":
        image_path = resolve_path(args.image)
        if not image_path.is_file():
            print(
                f"Error: Apptainer image does not exist: {image_path}",
                file=sys.stderr,
            )
            return ExitCode.GENERAL_ERROR
    else:
        if (
            args.image.endswith((".tar", ".tar.gz", ".tar.bz2"))
            or args.image.startswith(("./", "../", "/"))
        ) and not Path(args.image).exists():
            print(
                f"Error: Specified image file does not exist: {args.image}",
                file=sys.stderr,
            )
            return ExitCode.GENERAL_ERROR

    try:
        if args.copy_config:
            print(
                f"Copying configuration from "
                f"{resolve_path(args.copy_config)} to {config_dir}"
            )
            copy_into_directory(args.copy_config, config_dir)
        elif args.reset_config:
            print(f"Resetting configuration directory: {config_dir}")
            reset_directory(config_dir)
        else:
            ensure_directory(config_dir)

        if args.copy_data:
            print(
                f"Copying OpenCode data from "
                f"{resolve_path(args.copy_data)} to {data_dir}"
            )
            copy_into_directory(args.copy_data, data_dir)
        elif args.reset_data:
            print(f"Resetting data directory: {data_dir}")
            reset_directory(data_dir)
        else:
            ensure_directory(data_dir)

    except (FileNotFoundError, RuntimeError, OSError) as error:
        print(f"Error preparing sandbox directories: {error}", file=sys.stderr)
        return ExitCode.GENERAL_ERROR

    config_file = find_config_file(config_dir)
    if config_file is None:
        default_config_path = config_dir / "opencode.json"
        if not default_config_path.exists():
            default_config_path.write_text(
                '{\n  "$schema": "https://opencode.ai/config.json"\n}'
            )
        config_file = default_config_path

    if args.install_kai:
        ensure_directory(config_dir)
        install_kai(config_dir)
        config_file = find_config_file(config_dir) or config_file

    container_env = build_container_environment(config_file, env_file=env_file)

    print("==========================================================")
    print(f"OpenCode Container Sandbox ({args.engine.capitalize()})")
    print("==========================================================")
    print(f"Engine:             {args.engine}")
    print(f"Image:              {args.image}")
    print(f"Project:            {project_dir}")
    print(f"Configuration:      {config_dir}")
    print(f"Persistent data:    {data_dir}")
    if env_file is not None:
        print(f"Environment file:   {env_file}")
    else:
        print("Environment file:   none")

    if config_file is not None:
        print(f"Primary config:     {config_file}")
    else:
        print("Primary config:     none")
        print(
            "Warning: No opencode.json or opencode.jsonc was found. "
            "OpenCode will start with an empty opencode.json file."
        )

    print("==========================================================")

    subprocess_env = os.environ.copy()

    if args.engine == "apptainer":
        for name, value in container_env.items():
            subprocess_env[f"APPTAINERENV_{name}"] = value
            subprocess_env[f"SINGULARITYENV_{name}"] = value
        command = build_apptainer_command(
            image_path=resolve_path(args.image),
            project_dir=project_dir,
            config_dir=config_dir,
            data_dir=data_dir,
            opencode_args=args.opencode_args,
            debug_container=args.debug_container,
        )
    else:
        for name, value in container_env.items():
            if name not in PROTECTED_CONTAINER_VARS:
                subprocess_env[name] = value
        command = build_podman_command(
            image=args.image,
            project_dir=project_dir,
            config_dir=config_dir,
            data_dir=data_dir,
            container_env=container_env,
            opencode_args=args.opencode_args,
            debug_container=args.debug_container,
        )

    try:
        completed_process = subprocess.run(
            command,
            env=subprocess_env,
            check=False,
        )
        return completed_process.returncode

    except KeyboardInterrupt:
        print("\nOpenCode execution interrupted.", file=sys.stderr)
        return ExitCode.INTERRUPTED

    except OSError as error:
        print(
            f"Error launching {args.engine}: {error}",
            file=sys.stderr,
        )
        return ExitCode.GENERAL_ERROR


if __name__ == "__main__":
    sys.exit(main())
