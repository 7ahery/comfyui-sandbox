# Commands that change the sandbox end with ComfyUI stopped

`config`, every `update` target, `update all`, `node install`, `restore`, `rebuild` and `recreate` stop ComfyUI if it is running, do their work, and end with it stopped, even when the work failed (`steps.ending_stopped`). A step that needs the container running starts it just for that step. Each command ends by saying ComfyUI is stopped and to run `comfyui-sandbox start`. "Always end stopped" was chosen over "leave it as found", so every command ends in one known state. An open run window isn't asked about: stopping ComfyUI closes it, and the output says so and how to reopen it.

## Consequences

- `config` changes only files. The new values apply at the next `start`, so a bad launch flag or a port compose can't bind shows up there, not at `config`.
- Not under the rule: `start`, `stop`, `run` and `uninstall` (changing the state is their job); `backup`, which changes nothing and leaves ComfyUI as it found it, because interrupting a working session for a backup would be a surprise; the read-only commands and the passthroughs.
