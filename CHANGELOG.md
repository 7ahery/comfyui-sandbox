# Changelog

All notable changes to this project are listed here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). A major version may
break the command line or the files the tool writes.

## [Unreleased]

### Added

- A one-line install: `install.ps1` installs uv with winget when it is missing, then installs the tagged release with `uv tool install` and puts it on PATH.

### Fixed

- Every new ComfyUI run checks sandbox isolation before launching. Failed or inconclusive checks show an advisory warning and ComfyUI still starts. The run window keeps the warning visible while ComfyUI runs.
- Hardening leaves shared WSL interop handlers alone and removes the boot helper from early PR builds on install or resume. `verify` checks both persisted interop settings and reports unavailable or enabled handler state as unverified isolation, with exit 1, rather than claiming Windows execution is enabled or silently passing an unreadable registry. A reliable execution probe for an enabled shared handler remains pending.
- `check` rejects a known bundled WSL kernel older than the supported minimum of 6.6, and `prereqs` offers an update. Kernel detection works with localized output. Unknown versions and custom kernels report compatibility as unverified without repeatedly updating WSL or changing the custom setting.
- A step that fails with `Wsl/Service/E_UNEXPECTED` keeps its exit code and original error detail, and adds a conditional hint to check WSL and update it when needed.

## [1.0.0] - 2026-10-17

The first public release. It ships:

### Added

- `comfyui-sandbox`, a Windows CLI that builds an isolated ComfyUI: a dedicated WSL2 distro with
  automount and interop off, Docker Engine, the NVIDIA Container Toolkit, and ComfyUI in a
  non-root container with every capability dropped and its port on `127.0.0.1` only.
- `home`, a full-screen terminal UI over the same core as the CLI.
- Setup and health: `check`, `prereqs`, `install` (resumable), `verify`.
- Everyday use: `run`, `start`, `stop`, `open`, `status`, `logs`.
- Changes and repairs: `config`, `rebuild`, `reinstall`, `update`, `uninstall`.
- Pass-throughs into the sandbox: `comfy`, `cm-cli`, `exec`.
- Every question has a flag, `--yes` and `--non-interactive` for scripts, and `--json` for one
  machine-readable result per command.
- Destructive actions ask for the distro name before they run.
- NVIDIA GPUs, with a CPU-only fallback on other hardware.
