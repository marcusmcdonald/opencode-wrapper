# opencode-wrapper

Run [OpenCode](https://opencode.ai) inside an isolated container sandbox built from a single `Dockerfile`, with support for both **Apptainer** and **Podman**. The project provides two tools:

- **`opencode`** (`opencode_wrapper.cli`) — runs OpenCode inside the container sandbox, mounting your project and keeping configuration/data persistent on the host.
- **`opencode-build`** (`opencode_wrapper.build_image`) — builds the container image (Apptainer `.sif` or Podman image) from the included `Dockerfile`.

## Installation

Install as a global CLI tool using `uv tool`:

```bash
uv tool install -e .
```

This exposes `opencode` and `opencode-build` directly in your `PATH` (in `~/.local/bin`).

Or install in editable mode in a local virtual environment:

```bash
uv sync               # sets up .venv and installs project in editable mode
# or
uv pip install -e .
```

> [!NOTE]
> `pyproject.toml` maps the `opencode` entry point to the wrapper, shadowing any globally installed bare OpenCode binary.

## How it works

The image contains OpenCode, Astral `uv`, Git, and clipboard/X11 helpers on top of `ubuntu:24.04` (see `Dockerfile`). The wrapper launches OpenCode inside the container with:

- Your current directory (or a given project directory) mounted read-write at `/workspace/project`
- A host config directory mounted at `/workspace/opencode-config` (sets `OPENCODE_CONFIG_DIR` / `OPENCODE_CONFIG`)
- A host data directory mounted at `/workspace/opencode-data/opencode` (sets `XDG_DATA_HOME`; this is where auth is stored)

Because each sandbox keeps its configuration and data in an isolated host directory, you can run OpenCode instances without clobbering your normal `~/.config/opencode` or `~/.local/share/opencode`.

## Requirements

- Python 3.12+
- Astral `uv`
- **Apptainer** and/or **Podman** installed and in `PATH`
- `python-dotenv` and `spython` (automatically installed as dependencies)

## Building the image

`opencode-build` auto-detects the build engine (Podman is preferred when both are present).

```bash
# Auto-detect engine and build
opencode-build

# Or with uv run
uv run opencode-build

# Build to a specific SIF path (Apptainer)
opencode-build --engine apptainer -o opencode.sif

# Build with Podman under a custom tag
opencode-build --engine podman -o opencode:latest

# Disable build cache
opencode-build --no-cache
```

Available options: `-e/--engine {auto,apptainer,podman}`, `-f/--file` (Dockerfile path, default bundled `Dockerfile`), `-o/--output` (`.sif` path for Apptainer, image tag for Podman), `--no-cache`.

Default outputs: `~/.local/share/opencode-wrapper/opencode.sif` (Apptainer) or `opencode:latest` (Podman tag).

## Running OpenCode

```bash
# Run in the current directory
opencode

# Or with uv run
uv run opencode

# Run against a specific project directory, passing OpenCode args through
opencode /path/to/myproject "do something"

# Force a specific engine
opencode --engine apptainer
opencode --engine podman

# Use a specific image path or tag
opencode --image ./opencode.sif
opencode --image opencode:latest
```

The engine is auto-detected: a `.sif`/`.simg` image (or any file path) selects Apptainer; an image tag selects Podman; otherwise the default image for the detected runtime is used (e.g. `~/.local/share/opencode-wrapper/opencode.sif` for Apptainer).

### Configuration and data management

Config and data live by default in `~/.local/share/opencode-wrapper/config/opencode` and `~/.local/share/opencode-wrapper/data/opencode` (overrideable with `--sandbox-config-dir` / `--sandbox-data-dir` or `$OPENCODE_WRAPPER_ROOT`).

```bash
# Copy an existing config file/dir into the sandbox before launching
opencode --copy-config ~/.config/opencode/opencode.json

# Copy existing data (e.g. auth.json) into the sandbox
opencode --copy-data ~/.local/share/opencode

# Start from a clean slate
opencode --reset-config --reset-data

# Point the sandbox at custom host directories
opencode --sandbox-config-dir /tmp/my-config --sandbox-data-dir /tmp/my-data
```

If no `opencode.json`/`opencode.jsonc` exists in the config dir, an empty one is created before launching. API keys (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`) and Wayland/X11 environment are passed through from the host when present.

### Environment variables and `.env` files

By default, the wrapper looks for a `.env` file in `~/.local/share/opencode-wrapper/.env` first, and if not found, falls back to checking the target project directory. You can also specify an explicit file or directory using `--env-file`:

```bash
# Load from a specific .env file
opencode --env-file /path/to/custom.env

# Load from a directory (assumes .env inside that directory)
opencode --env-file /path/to/project-folder/
```

All variables defined in the `.env` file are forwarded into the container sandbox (internal container paths like `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG`, and `XDG_DATA_HOME` are protected). If an environment variable is already set in the host shell environment, the host shell value takes precedence over the value in `.env`. Sensitive variables are passed securely without exposing secret values on the host command line (`/proc/<pid>/cmdline`).

### Other options

- `--env-file PATH` — path to a `.env` file or directory containing `.env`. Defaults to checking `~/.local/share/opencode-wrapper/.env` then the project directory.
- `--sandbox-config-dir PATH` — host directory used to persist OpenCode configuration (default: `~/.local/share/opencode-wrapper/config/opencode`).
- `--sandbox-data-dir PATH` — host directory used to persist OpenCode data and authentication (default: `~/.local/share/opencode-wrapper/data/opencode`).
- `--install-kai` — download and install Kai multi-agent orchestration into the sandbox configuration directory, configuring `kai` as the default agent and disabling `build` and `plan`.
- `--debug-container` — print container identity, environment, mounts, and resolved OpenCode config, then exit.
- `-v/--version` — show wrapper version and the OpenCode version from inside the container.
- `-h/--help` — show wrapper help plus OpenCode's own `--help` from inside the container.

## Development

Install development dependencies:

```bash
uv sync --group dev   # installs ruff, pre-commit, and pyright
```

### Pre-commit hooks

Install the git hooks into the repository:

```bash
uv run pre-commit install
```

Once installed, hooks will run automatically on staged files whenever you run `git commit`.

To manually run hooks against all files:

```bash
uv run pre-commit run --all-files
```

### Linting & Formatting

```bash
uv run ruff check     # check for lint errors
uv run ruff format    # check / apply formatting
```

### Type Checking

```bash
uv run pyright        # run static type checking
```

## License

See [LICENSE](LICENSE).
