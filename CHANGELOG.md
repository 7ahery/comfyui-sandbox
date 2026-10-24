# Changelog

All notable changes to this project are listed here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). A major version may
break the command line or the files the tool writes.

## [Unreleased]

### Fixed

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
