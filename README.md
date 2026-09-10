# opencode-wrapper

Run [OpenCode](https://opencode.ai) inside an isolated container sandbox built from a single `Dockerfile`, with support for both **Apptainer** and **Podman**. The project provides two tools:

- **`build_image.py`** — builds the container image (Apptainer `.sif` or Podman image) from the included `Dockerfile`.
- **`opencode_wrapper.py`** — runs OpenCode inside that image, mounting your project and keeping configuration/data persistent on the host.

## How it works

The image contains OpenCode, Astral `uv`, Git, and clipboard/X11 helpers on top of `ubuntu:24.04` (see `Dockerfile`). The wrapper launches OpenCode inside the container with:

- Your current directory (or a given project directory) mounted read-write at `/workspace/project`
- A host config directory mounted at `/workspace/opencode-config` (sets `OPENCODE_CONFIG_DIR` / `OPENCODE_CONFIG`)
- A host data directory mounted at `/workspace/opencode-data/opencode` (sets `XDG_DATA_HOME`; this is where auth is stored)

Because each sandbox keeps its configuration and data in an isolated host directory, you can run OpenCode instances without clobbering your normal `~/.config/opencode` or `~/.local/share/opencode`.

## Requirements

- Python 3.12+
- **Apptainer** and/or **Podman** installed and in `PATH`
- `spython` for Apptainer builds from Dockerfiles: `uv pip install spython` (declared in `pyproject.toml`)

## Building the image

`build_image.py` auto-detects the build engine (Podman is preferred when both are present).

```bash
# Auto-detect engine and build
python build_image.py

# Build to a specific SIF path (Apptainer)
python build_image.py --engine apptainer -o opencode.sif

# Build with Podman under a custom tag
python build_image.py --engine podman -o opencode:latest

# Disable build cache
python build_image.py --no-cache
```

Available options: `-e/--engine {auto,apptainer,podman}`, `-f/--file` (Dockerfile path, default `./Dockerfile`), `-o/--output` (`.sif` path for Apptainer, image tag for Podman), `--no-cache`.

Default outputs: `opencode.sif` (Apptainer) or `opencode:latest` (Podman tag).

## Running OpenCode

```bash
# Run in the current directory
python opencode_wrapper.py

# Run against a specific project directory, passing OpenCode args through
python opencode_wrapper.py /path/to/myproject "do something"

# Force a specific engine
python opencode_wrapper.py --engine apptainer
python opencode_wrapper.py --engine podman

# Use a specific image path or tag
python opencode_wrapper.py --image ./opencode.sif
python opencode_wrapper.py --image opencode:latest
```

The engine is auto-detected: a `.sif`/`.simg` image (or any file path) selects Apptainer; an image tag selects Podman; otherwise the default image for the detected runtime is used.

### Configuration and data management

Config and data live by default in `./config/opencode` and `./config/.local/share/opencode` relative to this project.

```bash
# Copy an existing config file/dir into the sandbox before launching
python opencode_wrapper.py --copy-config ~/.config/opencode/opencode.json

# Copy existing data (e.g. auth.json) into the sandbox
python opencode_wrapper.py --copy-data ~/.local/share/opencode

# Start from a clean slate
python opencode_wrapper.py --reset-config --reset-data

# Point the sandbox at custom host directories
python opencode_wrapper.py --sandbox-config-dir /tmp/my-config --sandbox-data-dir /tmp/my-data
```

If no `opencode.json`/`opencode.jsonc` exists in the config dir, an empty one is created before launching. API keys (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`) and Wayland/X11 environment are passed through from the host when present.

### Other options

- `--install-kai` — download and install the Kai multi-agent orchestration into the sandbox config directory.
- `--debug-container` — print container identity, environment, mounts, and resolved OpenCode config, then exit.
- `-v/--version` — show wrapper version and the OpenCode version from inside the container.
- `-h/--help` — show wrapper help plus OpenCode's own `--help` from inside the container.

## Installation

```bash
uv pip install -e .   # exposes the `opencode` console script
```

Note: `pyproject.toml` maps the `opencode` entry point to the wrapper, so it shadows any globally installed OpenCode binary.

## Development

```bash
uv sync --group dev   # installs ruff and pre-commit
```

## License

See [LICENSE](LICENSE).