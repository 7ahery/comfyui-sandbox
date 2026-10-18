# The sandbox's safety rules are enforced in code

The point of the project is an isolated ComfyUI, so these rules hold everywhere and the CLI enforces them. Don't relax one to make a feature easier: a change that needs to is a new decision.

- Never weaken isolation: no automount or interop in the distro, no extra Windows mounts, no binding to `0.0.0.0`, no `privileged`, host networking, extra capabilities or `docker.sock`. `config` refuses values that would do any of these (`commands.config.check_isolation`).
- Never type, store or echo passwords. The Linux user has none; root tasks use `wsl -u root`.
- Never install a Linux NVIDIA driver in the distro.
- Prefer `wsl --terminate <distro>`. `wsl --shutdown` stops the user's other distros, so it needs confirmation ([0009](0009-destructive-actions-need-the-sandbox-name.md)).
- Never touch a distro that isn't in `installs.json` ([0005](0005-installs-json-records-and-env-decides.md)).
- The uninstall report prints counts only, never file names or paths.
