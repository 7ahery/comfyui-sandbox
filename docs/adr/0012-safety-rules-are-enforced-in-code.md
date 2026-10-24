# The sandbox's safety rules are enforced in code

The point of the project is an isolated ComfyUI, so these rules hold everywhere and the CLI enforces them. Don't relax one to make a feature easier: a change that needs to is a new decision.

- Never weaken isolation: no automount or interop in the distro, no extra Windows mounts, no binding to `0.0.0.0`, no `privileged`, host networking, extra capabilities or `docker.sock`. `config` refuses values that would do any of these (`commands.config.check_isolation`).
- Never type, store or echo passwords. The Linux user has none; root tasks use `wsl -u root`.
- Never install a Linux NVIDIA driver in the distro.
- Prefer `wsl --terminate <distro>`. `wsl --shutdown` stops the user's other distros, so it needs confirmation ([0009](0009-destructive-actions-need-the-sandbox-name.md)).
- Never touch a distro that isn't in `installs.json` ([0005](0005-installs-json-records-and-env-decides.md)).
- The uninstall report prints counts only, never file names or paths.

## Temporary exception: startup isolation warnings

Until a distro-scoped remedy for WSL interop is available and validated, each new ComfyUI run checks isolation, warns if it fails or cannot be verified, and still launches. The run window keeps the warning visible. WSL interop handlers can be shared across distros, so disabling a shared handler would violate the ownership boundary above. The sandbox keeps its own interop settings disabled and leaves shared handlers alone.

This exception applies only to startup gating. Full `verify` and install's verification step still fail when isolation fails or cannot be verified. Remove this exception and restore startup enforcement once the remedy establishes isolation within the sandbox without affecting other distros. The startup behavior is specified in [docs/scripting.md](../scripting.md#run-start-stop-and-open).

Related: [PR #2](https://github.com/7ahery/comfyui-sandbox/pull/2).
