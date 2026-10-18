# `installs.json` records what the CLI owns; `.env` decides the settings

`%USERPROFILE%\.comfyui-sandbox\installs.json` lists the distros the CLI created, with their saved intake answers. It is the ownership boundary: the CLI never touches a distro that isn't in it. `.env` inside the distro stays the source of truth for runtime settings. The record's `port` and `models` are only the values last applied: commands that apply settings read `.env` and then update the record, and commands that report on a running instance show the record, because a running ComfyUI keeps the settings it started with. The record exists so other installs can be checked (for example for a port collision) without starting them; nothing else may depend on it being current.

## Consequences

- A missing `installs.json` means "reinstall": the CLI doesn't adopt distros it didn't create.
- Each record keeps the CLI version that deployed it (`deployed_by`). `status` warns when it is behind; `update setup` redeploys the assets, merges new keys into `.env` (keeping a backup and the user's own values) and rebuilds. Upgrades are never automatic.
