# Security policy

comfyui-sandbox exists to put a boundary between ComfyUI (and every custom node you install)
and your Windows machine. A way through that boundary is a security bug, and reports are welcome.

## Supported versions

Only the latest release, and `main`, get security fixes.

## Reporting a vulnerability

**Please don't open a public issue for a security problem.**

Report it privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability**. Include:

- what the problem lets code inside the sandbox (or another local user) do,
- the steps to reproduce it, and the comfyui-sandbox, Windows and WSL versions,
- the output of `comfyui-sandbox verify` if it is relevant. Check logs for anything personal
  before you attach them.

You should get an answer within 7 days. Once a fix is out, the report is published as an
advisory, with credit to you unless you'd rather stay anonymous.

## What counts

The isolation model is described in [ADR 0012](docs/adr/0012-safety-rules-are-enforced-in-code.md). In short, these are in scope:

- **Container escapes**: code in the ComfyUI container gaining root in the distro, more
  capabilities than it was given, or access beyond the `data/` and models mounts.
- **Network exposure**: the ComfyUI port reachable from anywhere but `127.0.0.1` on the host.
- **Reaching Windows**: anything that lets the distro mount Windows drives or launch Windows
  programs, against the settings the tool applies, without root inside the distro.
- **The tool itself**: command injection through answers, flags or file contents, a destructive
  action that runs without its confirmation, or `prereqs` elevating more than it says.
- Tests or checks in `verify` that report a hardening setting as on when it is off.

These are out of scope:

- Bugs in ComfyUI, ComfyUI-Manager, comfy-cli or custom nodes. Please report them upstream.
- Bugs in WSL, Docker or the NVIDIA Container Toolkit, unless the tool configures them unsafely.
- Root inside the distro undoing the WSL settings. The Linux user is in the `docker` group, so it
  counts as root, and the WSL layer is documented as defense in depth, not a wall.
- Anything that needs an administrator on the Windows host.
