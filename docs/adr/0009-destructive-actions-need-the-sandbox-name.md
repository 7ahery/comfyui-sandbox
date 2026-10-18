# Destructive actions need the sandbox's name, never `--yes`

`uninstall`, `restore`, `recreate`, and a `wsl --shutdown` that would stop distros the CLI doesn't own all go through one gate (`destructive.confirm_destructive`). They need `--confirm <sandbox>` (for a shutdown, the name of a distro it would stop), or the name typed at a prompt. `--yes` never confirms them, and `--non-interactive` without `--confirm` exits 8. A script or an agent that passes `--yes` to get past ordinary questions must never wipe a sandbox by accident.
