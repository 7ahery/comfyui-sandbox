# Scripting comfyui-sandbox

What scripts and agents can rely on when they drive the CLI: its exit codes, streams and `--json` document, how it
asks for answers, and the files it keeps. A major version may change it, and `CHANGELOG.md` says so
when it does. How to use the commands is in the [README](../README.md), why things are this way is in `docs/adr/`,
and the rules the TUI keeps are in [`tui.md`](tui.md).

The CLI calls the thing it manages a **sandbox**: one isolated ComfyUI, in a WSL distro the CLI created and records
in `installs.json`. The names callers type or parse keep their older words: `--distro`, `installs.json` and the
`--json` keys. The terms are defined in [`GLOSSARY.md`](../GLOSSARY.md).

## Driving it from a script or agent

- Pass `--json --non-interactive`. Every invocation then prints exactly one JSON document on stdout and never
  prompts; progress and warnings go to stderr.
- Branch on the exit code (also `exit_code` in the document). `message` is the sentence to show a person.
- **9**: an answer is missing. Ask the user, then run the command again with the flag named in `flag` (`--port`,
  `--distro`, an install question's flag).
- **8**: a destructive action needs `--confirm <name>`: the sandbox's name, or for a WSL shutdown a running
  distro's. Pass it only once the user has confirmed that name.
- **10**: not ready yet; safe to retry. **3 to 7**: the machine isn't ready; `check` says why, and `prereqs` sets
  up WSL (7).
- On a failure, `log` is the transcript to read; `logs --cli --last` prints the newest one.
- With more than one sandbox, pass `--distro`. Their names are the `distro` fields in `installs.json`.
- Reach inside a sandbox with the `comfy`, `cm-cli` and `exec` passthroughs, never with raw `wsl.exe` or `docker`
  calls ([ADR 0001](adr/0001-one-cli-for-people-scripts-and-agents.md)).

## Exit codes

| Code | Name (`ExitCode.*`) | Meaning |
|---|---|---|
| 0 | `OK` | Success. |
| 1 | `FAILED` | Failed. The message says why; see the transcript (`"log"`) when there is one. Also any unexpected exception. |
| 2 | `USAGE` | Usage error: unknown command or option, bad option value, missing argument, a refused `config` value. Also a bare `comfyui-sandbox` or `home` where home can't open (see "The TUI"). |
| 3 | `NO_NVIDIA` | No NVIDIA GPU. CPU-only is possible. |
| 4 | `UNSUPPORTED_HOST` | Unsupported host (not Windows). |
| 5 | `VIRTUALIZATION_OFF` | Virtualization off in firmware. |
| 6 | `NO_DRIVER` | NVIDIA GPU without a working driver. |
| 7 | `WSL_NOT_READY` | WSL missing, too old, or Virtual Machine Platform off. Run `prereqs`. |
| 8 | `DESTRUCTIVE_REFUSED` | Destructive action refused: `--confirm <sandbox>` missing, or the name did not match. |
| 9 | `ANSWER_MISSING` | An answer is missing and the CLI can't ask (`--non-interactive`, or a preflight conflict under `--yes`). The message names the flag to pass; JSON also gives it in `"flag"`. |
| 10 | `NOT_READY` | Not ready yet (timeout). Safe to retry. |
| 130 | `INTERRUPTED` | Interrupted by the user (Ctrl+C, or a prompt cancelled). The message is `Interrupted.`, and a transcript started before it ends with `exit=130`. |

- `check` reports the highest-priority failure: host, virtualization and WSL failures (4, 5, 7) win over the GPU
  codes (6, 3).
- The passthroughs (`comfy`, `cm-cli`, `exec`) exit with the inner command's code, which can be any number.

## Streams

- **stdout holds the result and nothing else.** Under `--json` that is exactly one JSON document followed by a
  newline. Without `--json`, it is the result message, or nothing. For `logs` the message is what it read
  (`--follow` prints the lines as they come), and a passthrough prints its command's own stdout.
- **stderr holds everything for people:** progress, warnings, prompts, error messages, the transcript path on failure.
  Its wording and layout can change at any time; parse stdout.
- Exception: `--help` prints help to stdout and no JSON document.

## The `--json` result document

One document on stdout per invocation, also for usage errors and crashes. UTF-8, a single JSON object.

| Key | Type | Present | Meaning |
|---|---|---|---|
| `command` | string | always | The command words joined by spaces: `"version"` for `--version`, `"check"`, `"update comfyui"`, `"node install"`. `""` when no command could be determined. |
| `ok` | bool | always | `true` exactly when `exit_code` is 0. |
| `exit_code` | int | always | The process exit code. |
| `message` | string | always | One human sentence summarizing the result (the same text people see). |
| `data` | object | always | Command-specific payload (see "Per-command `data`"). `{}` when there is none. A failure may still carry data. |
| `log` | string | only when a transcript was written | Absolute Windows path of the transcript. |
| `error` | string | only when `ok` is false | The same text as `message`. |
| `flag` | string | only for exit 9 (and refused `config` values) | The flag that answers the question, e.g. `"--port"`. |

No other top-level keys. New command-specific fields go inside `data`.

## Global options and choosing a sandbox

`--json`, `--yes`, `--non-interactive`, `--distro <name>` (also `--distro=<name>`).

- They are accepted anywhere on the command line, up to a `--` token. For the passthroughs `comfy`, `cm-cli` and
  `exec`, they are only recognized before the command name, so everything after it is passed through untouched.
- `--version` and `--help` are root options; `--version` is not global, because `update comfyui --version` uses the name.
- `--yes` accepts every default. It never confirms a destructive action and never answers a preflight conflict.
- `--non-interactive` never prompts; a missing answer exits 9.
- With no command, they decide whether home opens (see "The TUI").

Which sandbox a command acts on:

- `--distro` given: that sandbox (names compare case-insensitively). A distro not in `installs.json` is refused
  (exit 1); with no `installs.json` at all the message says to install one.
- Not given: the only sandbox. With several, a terminal asks "Which sandbox?"; under `--non-interactive` it exits 9
  with `flag` `--distro`. `--yes` does not pick one.
- A recorded distro that WSL no longer has (removed with `wsl --unregister` or Windows Settings) is refused with exit 1
  and a message to run `comfyui-sandbox uninstall --distro <name>`, which then removes only its shortcuts and its entry.
- Exceptions: `install`, where `--distro` names the new distro (or the unfinished install to resume); and
  `launcher remove`, which may name a distro no longer in `installs.json` and only ever deletes shortcuts named
  `ComfyUI (<name>).lnk`.

## Answering questions

Every question is a flag, and a flag skips its question ([ADR 0008](adr/0008-every-question-is-a-flag.md)). Each
command's `--help` lists its flags; the install questions' values and defaults are in the README.

- Under `--non-interactive`, the first unanswered question exits 9 naming its flag. `--yes` takes every default
  instead, except where a default would be a guess: a preflight conflict (a taken port, `--port`; an existing distro
  name, `--distro`; the recorded port of every other sandbox counts as taken, even while it is stopped) still exits
  9. `--location` is only asked when C: is low on space and another drive has room; then `--yes` picks that drive
  and `--non-interactive` alone exits 9 for it.
- An invalid value exits 2 naming the flag.
- `install` resumes the only unfinished install. `--new` starts another instead (naming an unfinished one with
  `--distro` as well exits 1). A resume uses the saved answers: `--channel`, `--manager-ui`, `--security` and
  `--port` change them, and any other install question's flag exits 2. `--change` asks those questions again, so
  under `--non-interactive` it exits 9; without an unfinished install it exits 2.
- `prereqs` asks before it elevates: `--yes` approves, `--non-interactive` without it exits 9, and declining (or
  declining UAC) is exit 1.
- `config` with no flags shows a menu at a terminal; under `--yes` or `--non-interactive` it exits 2. A value it
  refuses, such as one that would weaken the isolation
  ([ADR 0012](adr/0012-safety-rules-are-enforced-in-code.md)), exits 2 with `flag` set, and nothing is changed.

## Per-command `data`

The keys a successful command's `data` holds. Each row lists every key that command may put at the top of its
`data`. A failure's `data` is best effort. New keys may be added; a key is never removed or renamed without a `CHANGELOG.md` entry. Commands
under "Commands that change the sandbox end stopped" also carry `container` and `run_window_closed`.

| Command | `data` |
|---|---|
| `--version` | `{"version"}` |
| `check` | See below. Present on every exit code; on exit 4 only `host`, `problems` and `warnings`. |
| `prereqs` | `{"action": "install"\|"update"\|"none", "reboot_needed": bool}`, also when declined (exit 1). |
| `install` | `{"distro", "resumed", "install": <the installs.json entry>, "wsl_restart_pending", "verify": <verify's data>, "launcher": {"shortcuts", "refused"}, "start": <start's data>, "summary": {"url", "gpu", "wsl_ram_gb", "pinned_memory", "project", "models", "runs_only_while_window_open"}}` |
| `verify` | `{"distro", "checks": [{"section", "status", "message", "hint"}], "counts": {"pass", "warn", "fail"}, "windows_reach"}` |
| `run` | `{"distro", "url", "ended": "stopped"}`, or `{"distro", "url", "already_running": true}` when a run window was already open |
| `start` | `{"distro", "url", "already_running"}` |
| `stop` | `{"distro"}` |
| `open` | `{"distro", "url"}`; when it isn't running, exit 1 with `{"distro", "windows_reach"}` |
| `status` | `{"distro", "container", "ready", "url", "run_window", "deployed_by", "cli_version", "drift", "windows_reach", "log_tail"}`, plus `env_port` when `.env` names a port other than the record's |
| `logs` | `{"distro", "tail", "follow", "log"}` (`log` only without `--follow`). Exits 10 (`NOT_READY`) without reading anything when the distro isn't running: it never starts one |
| `logs --cli [--last]` | `{"transcripts": [paths, newest first]}`, with `--last` also `"path"` and `"content"` (null when there are none) |
| `update` (bare) | `{"distro", "targets": [{"target", "pending": true\|false\|null, "detail", "current", "available", "pinned"}]}`; `null` means unknown and `detail` starts with `unknown:`; `pinned` is the release a pinned ComfyUI is held on (else `null`), and a pinned target is never pending |
| `update all` | `{"distro", "targets": {<name>: <that target's data>}}`; a failure adds `completed` and `failed` |
| `update comfyui` | `{"distro", "version", "pinned", "previous", "installed", "env_changed"}`: `version` is the channel, `pinned` the release it is pinned to (else `null`), `previous` and `installed` the versions before and after (`null` when unreadable). Without `--version` on a pinned sandbox nothing runs and ComfyUI is left as it was (`left_pinned` is `true`; `previous` and `installed` are `null`) |
| `update nodes`, `update comfy-cli` | `{"distro"}` |
| `update system` | `{"distro", "packages", "nvidia_toolkit"}` |
| `update setup` | `{"distro", "deployed_by", "env_added": [keys], "backup"}` |
| `node install` | `{"distro", "node", "via": "comfy-cli"\|"cm-cli", "imported", "new_folders"}`; an import failure adds `import_errors` |
| `config` | `{"distro", "changes": {KEY: {"from", "to"}}, "backup", "security"}` |
| `backup` | `{"distro", "name", "path", "snapshot", "restore", "started_comfyui"}`; `restore` is the command line that restores this backup (`comfyui-sandbox restore --distro <d> --backup <name>`), also named in the message |
| `restore` | `{"distro", "name", "path"}` |
| `rebuild`, `reinstall` (and its old name `recreate`) | `{"distro"}` |
| `wslconfig show` | `{"path", "exists", "content", "limits": {"host_ram_gb", "host_cpus", "memory_gb", "memory_low_gb", "processors", "swap_gb"}}` |
| `wslconfig apply` | `{"path", "limits": {"memory_gb", "processors", "swap_gb"}, "changed", "backup", "restart": "not_needed"\|"now"\|"later", "restart_pending"}` |
| `launcher install` / `remove` | `{"distro", "shortcuts"}` / `{"distro", "removed"}` |
| `uninstall` | `{"distro", "unregistered", "shortcuts_removed", "report": {"workflows", "input_files", "output_files", "custom_nodes", "backups", "model_files", "models": "windows"\|"wsl"\|"unknown", "disk_used"}, "location", "wslconfig_kept"}`; a `report` key is there only when the uninstall could count it |
| `comfy`, `cm-cli`, `exec` | `{"exit_code", "stdout", "stderr"}` |

`windows_reach` is `{"ok", "url", "os", "message", "hint"}`. Failures of `run`, `start` and `open` to reach ComfyUI
from Windows carry it too.

`check`'s `data` (unknown values are `null`):

| Key | Meaning |
|---|---|
| `problems` | `[{"exit_code", "message"}]`: every failure found. |
| `warnings` | `[str]`, e.g. a Blackwell card, a driver too old for the card, no hypervisor running. |
| `host.platform` | `sys.platform`, `"win32"` on Windows. |
| `gpu.mode` | `"nvidia"`, `"nodriver"` (exit 6) or `"none"` (exit 3). |
| `gpu.gpus` | `[{"name", "driver", "vram_mib", "compute_capability"}]` from `nvidia-smi.exe`. |
| `gpu.driver_cuda` | Highest CUDA version the driver supports, e.g. `"12.9"`. |
| `gpu.cuda_version_suggested` | `"12.8"` for a Blackwell card whose driver supports it, else null. |
| `gpu.windows_gpus` | Exit 3: the GPUs Windows reports. Exit 6: the NVIDIA devices. |
| `virtualization` | `{"hypervisor", "firmware_enabled", "virtual_machine_platform": "enabled"\|"disabled"\|"absent"}` |
| `wsl` | `{"installed", "version", "ubuntu_26_04_available", "distros"}` |
| `drives` | `[{"drive": "C:", "free_gb"}]`, fixed local drives. |
| `c_free_gb`, `user_profile`, `win_user_suggested` | Free space on C:, `%USERPROFILE%`, and the lowercased `%USERNAME%` when it is a valid Linux user name. |
| `wsl_limits` | `{"host_ram_gb", "host_cpus", "memory_gb", "memory_low_gb", "processors", "swap_gb"}`, the suggested `.wslconfig` limits. |

## Commands that change the sandbox end stopped

`config`, every `update` target, `update all`, `node install`, `restore`, `rebuild` and `reinstall` stop ComfyUI if it
is running, do their work, and end with it stopped, also on failure
([ADR 0015](adr/0015-commands-that-change-the-sandbox-end-stopped.md)). Their `data`, and a failure's `data`, carry
`container` (`"stopped"`; `"running"` if the final stop failed, which is exit 1) and `run_window_closed`. Run `start`
afterwards. `backup` is the exception: it leaves ComfyUI as it found it.

## The destructive-action gate

It guards `uninstall`, `restore` and `reinstall`, and the `wsl --shutdown` that `install` and `wslconfig apply` run
with `--shutdown now` while distros the CLI doesn't own are running
([ADR 0009](adr/0009-destructive-actions-need-the-sandbox-name.md)). Nothing destructive runs before the gate.

| Situation | Result |
|---|---|
| `--confirm` names the sandbox (for a shutdown, a distro it would stop; case-insensitive) | confirmed, nothing asked |
| `--confirm` names something else | exit 8 |
| no `--confirm`, `--non-interactive` | exit 8, the message names `--confirm <sandbox>` |
| no `--confirm`, terminal (or home) | the warning, then "Type the sandbox name to confirm"; anything else is exit 8, backing out of the question exit 130 |
| `--yes` | ignored: it neither confirms nor skips the typing |

`restore` picks its backup before the gate. `--backup <name>` names it (one not in `~/comfyui-backups` is exit 1).
Without it, a terminal (or home) is asked which one, and backing out is exit 130 with nothing changed. Under `--yes`
or `--non-interactive`, the newest is taken unasked. With none, exit 1 before anything is asked, with `data`
`{"backups": []}`.

## `run`, `start`, `stop` and `open`

([ADR 0010](adr/0010-run-holds-comfyui-in-the-foreground.md))

- `run` blocks while ComfyUI runs, and whatever ends it stops the container. Its exit codes: 0 stopped cleanly; 1
  ComfyUI stopped on its own, compose failed, or Windows can't reach it; 10 not ready in time; 130 Ctrl+C. If a
  run window is already open for the sandbox, it only opens the browser and exits 0. At a terminal it shows the
  TUI's run window instead, with the same exit codes (see "The TUI").
- A sandbox has at most one run window open. `status`'s `data.run_window` says whether one is, and a window that
  crashed no longer counts.
- `start` opens a run window (unless one is open) and waits up to about 10 minutes for Windows to reach ComfyUI:
  exit 10 on timeout, exit 1 when the window closed early.
- `stop` stops the container, and an open run window closes.
- `open` opens the sandbox's ComfyUI in the browser and starts nothing. When the sandbox's own ComfyUI doesn't
  answer on its port, it opens nothing and exits 1.

## Transcripts

- Folder: `%USERPROFILE%\.comfyui-sandbox\logs\`. The document's `log` key gives the path of the one a command wrote.
- Written by every command that changes something. Read-only commands (`--version`, `check`, `status`, `logs`,
  `verify`, bare `update`, `wslconfig show`, `open`) write none.
- File name: `<UTC YYYYMMDDTHHMMSSZ>-<command words joined by '-'>.log`, e.g. `20261006T164800Z-update-comfyui.log`,
  with `-2`, `-3`, ... before `.log` on a collision. Names sort chronologically. Only the newest 20 are kept.
- UTF-8 without BOM. The content is for people to read: its layout can change. Nothing secret is ever logged.

## `installs.json`

`%USERPROFILE%\.comfyui-sandbox\installs.json`, UTF-8 without BOM. The list of sandboxes the CLI owns
([ADR 0005](adr/0005-installs-json-records-and-env-decides.md)); `install`'s `data.install` is one of its entries.
Read it, never write it: the CLI writes it atomically under a cross-process lock, applying only the changes the
current command made. A missing file means "no sandboxes"; a file that can't be parsed, or has a `schema` newer than
the CLI knows, is an error (exit 1) and is never overwritten.

```json
{
  "schema": 1,
  "installs": [
    {
      "distro": "comfy-docker",
      "linux_user": "comfy",
      "state": "complete",
      "port": 8188,
      "models": {"mode": "wsl", "path": "/home/comfy/comfyui-models"},
      "gpu": {"mode": "nvidia", "name": "NVIDIA GeForce RTX 4090"},
      "cuda_version": null,
      "location": null,
      "deployed_by": "1.0.0",
      "created_at": "2026-10-06T16:48:00Z",
      "updated_at": "2026-10-06T17:02:13Z",
      "distro_created_at": "2026-10-06T16:48:40Z",
      "answers": {}
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `schema` | File format version, currently `1`. |
| `distro` | WSL distro name. |
| `linux_user` | The passwordless Linux user. |
| `state` | `in_progress` from the moment the intake answers are saved until `install` finishes, then `complete`. |
| `port` | Host port on `127.0.0.1` as of the last start. `.env` `COMFY_PORT` is what the next start uses. |
| `models` | `mode` `wsl` (absolute Linux path) or `windows` (absolute Windows path, mounted at `/mnt/models`). As last applied. |
| `gpu` | `mode` `nvidia` or `cpu`; `name` as Windows reports it, null on CPU. |
| `cuda_version` | e.g. `"12.8"`, or null. |
| `location` | Windows folder the distro disk was installed to, or null for WSL's default. |
| `deployed_by` | CLI version that last deployed the assets (`install` or `update setup`). |
| `created_at`, `updated_at` | UTC, ISO 8601 with `Z`. |
| `distro_created_at` | When `install` created the distro; null before. |
| `answers` | The saved intake answers used to resume, one key per install flag in snake_case (`--models-path` is `models_dir`). |

New keys may be added: ignore the ones you don't know.

## The TUI

A second front end over the same core, for people at a terminal
([ADR 0017](adr/0017-two-front-ends-over-one-core.md)). Scripts, pipes and agents never see it. A bare
`comfyui-sandbox`, or `comfyui-sandbox home` (listed in `--help`), opens **home** when stdin and stdout are both a
terminal and neither `--json` nor `--non-interactive` is given:

| Command line | Result |
|---|---|
| bare or `home`, at a terminal | home opens; quitting it exits 0, as does Ctrl+C with nothing running; Ctrl+C that leaves an operation unfinished exits 130 |
| bare or `home`, stdin or stdout not a terminal | exit 2; the message says a terminal would open home |
| bare or `home`, with `--json` or `--non-interactive` | exit 2, the same message (one document under `--json`, `command` `""` for bare, `"home"` for `home`) |
| bare or `home`, with `--yes` or `--distro` | exit 2, at a terminal or not: home has no defaults to accept, and doesn't open on one sandbox |
| `run`, at a terminal, without `--plain` | the run window opens for the sandbox `run` resolved; its exit code is `run`'s |
| `run --plain`, or `run` with no terminal, `--json` or `--non-interactive` | the plain `run` |
