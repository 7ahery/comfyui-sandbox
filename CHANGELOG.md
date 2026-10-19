# Changelog

All notable changes to this project are listed here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). A major version may
break the command line or the files the tool writes.

## [Unreleased]

### Fixed

- `check` and `prereqs` reject a WSL kernel older than 6.6. WSL 2.4.10 can install Ubuntu 26.04 and then fail to start the distro. `prereqs` updates WSL.
- A step that dies with `Wsl/Service/E_UNEXPECTED` says the distro did not start and to run `prereqs`, instead of blaming that step's own work.

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
