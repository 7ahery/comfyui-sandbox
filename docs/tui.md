# Rules the TUI keeps

For anyone changing home or the run window. The screens explain themselves, so this lists only what a change
must keep, and why. When the TUI opens and how it exits are in [`scripting.md`](scripting.md) ("The TUI"). How it
is built is in the package docstrings, starting at `comfyui_sandbox/tui/__init__.py`. Why there are two front ends
over one core is [ADR 0017](adr/0017-two-front-ends-over-one-core.md).

`uv run python -m comfyui_sandbox.tui.demo` opens the screens on fake sandboxes that never touch WSL, Docker, a GPU
or `installs.json`. `--help` lists home's scenarios, and `run --help` the run window's.

## Actions are command lines

Every action home takes is a CLI command line, shown before it runs (`❯ comfyui-sandbox stop --distro x`) and run
exactly as shown, in-process (`cli.call`). So anything home does, a script can repeat, and home has no behavior of
its own: a new action is a CLI command first.

- It writes the transcript the same command writes from a terminal, and home writes none of its own while it runs.
- Home runs one action at a time.
- Home never answers a command's question for the user. Destructive actions pass no `--confirm`, and Restore passes
  no `--backup`: the command's own gate asks.
- Home's own questions only pick the command line: Update what? and Which ComfyUI? (its target and `--version`),
  the Yes/No before Update and Back up (which ask nothing themselves), and Set WSL up now? before `prereqs`.
- Commands that change the sandbox end with it stopped, in home as at a terminal.

| Action | Command line |
|---|---|
| Install a new sandbox | `install --new`, with no other flags, so its questions are intake's own |
| Resume install | `install --distro <name>` |
| Change answers and resume… | `install --distro <name> --change` |
| Open in browser | `open --distro <name>` |
| Start (in its own window) | `start --distro <name>` |
| Stop | `stop --distro <name>` |
| Run here | `run --distro <name>` |
| Show logs | `logs --follow --tail 500 --distro <name>` |
| Update… | `update <target> [--version <v>] --distro <name>` (not `all`, for now) |
| Back up | `backup --distro <name>` |
| Restore… | `restore --distro <name>` |
| Settings… | `config --distro <name>` |
| Reinstall ComfyUI… | `reinstall --distro <name>` |
| Uninstall… | `uninstall --distro <name>` |
| Set WSL up now? (after Check this PC, or an install, finds WSL missing) | `prereqs` |

Check this PC is the exception: it shows `❯ comfyui-sandbox check` but reads `check`'s findings through a gateway
query, not `cli.call`. `check` changes nothing and writes no transcript, so the two match.

What a sandbox's menu offers depends on its state:

- A Starting sandbox offers only Stop and Show logs. Its run window is bringing ComfyUI up, and anything else would
  race it.
- An Incomplete sandbox offers only Resume install, Change answers and resume… and Uninstall…. An unfinished install
  is resumed, not reinstalled, and nothing it has can run yet.
- Show logs is greyed out when the sandbox's distro isn't running: reading the log would start the distro and
  Docker.

## Questions

A command asks through the `Prompter` home gives it (`comfyui_sandbox.prompts`), so home asks exactly what the CLI
asks, in the same order, checked the same way. Home only draws them.

- Hints are display only. Home shows them, a terminal doesn't, and a hint never changes what counts as an answer.
- A type-the-name confirmation takes only the whole name. When several names would do, it's a plain text field. The
  command's own check of the answer is unchanged either way.
- Esc backs out of a question as Ctrl+C does at a terminal: the command ends as interrupted (130). Commands ask
  before they change anything, so backing out changes nothing. `install` saves nothing before its preflight passes.

## Cancelling and quitting

Nothing home starts is left running unseen.

- Home stops a command with a cancel switch (`comfyui_sandbox.cancel`): the running child process is terminated,
  the command unwinds through its own cleanup (keepalive released, container stopped, run-window marker freed), and
  it ends as interrupted, its transcript closed with `exit=130`. The CLI itself never uses one.
- Update, Back up, Restore, Reinstall ComfyUI and Uninstall can't be cancelled with Esc: stopping one halfway would
  leave the sandbox half-changed. Neither can the short actions shown under a spinner (Open in browser, Start, Stop,
  Settings, `prereqs`). An install can (Esc twice). It stays Incomplete, and Resume install carries on.
- Leaving Run here or Show logs ends the command the way Ctrl+C would. Stopping Run here takes a second press, and
  `run`'s own cleanup stops the container, so no `stop` runs.
- Ctrl+C during an operation warns that it'll be left unfinished, and a second press quits (130). However home
  quits, it first cancels every operation still running and waits until each has unwound. The screen says it's
  waiting, and one more Ctrl+C quits at once without waiting.
- After a failure home shows the error and stays open. It never waits for a key the way `run` in its own window
  does.
- A crash restores the terminal and is reported like any other crash: exit 1, and a transcript (`<stamp>-home.log`,
  `<stamp>-run.log`) holding the traceback.

## Home's state rule

A sandbox's state is the first of these that holds:

| State | When | On Windows, from |
|---|---|---|
| Incomplete | its install didn't finish | `installs.json`'s `state` is `in_progress` |
| Stopping | its run window is stopping ComfyUI (closed, Ctrl+C, or the container ended), whatever the port says | the run window's stopping marker is held |
| Starting | a run window is open for it, but its own ComfyUI doesn't answer yet | the run-window marker is held, and the reach check fails |
| Running | its own ComfyUI answers on its port, with or without a run window | `/system_stats` on `127.0.0.1:<port>` answers with `os` linux or posix |
| Stopped | anything else | |

A ComfyUI running on Windows (`os` win32 or nt), or any other program, on the port doesn't count as the sandbox's
own. While home's own command acts on a sandbox, its row shows that instead (installing, stopping...).

A row shows at most one note beside its state, never in place of it: the first of these that applies.

- **Pick it to resume**, on an Incomplete sandbox.
- **No run window**, on a Running sandbox with no run window open: closing a window won't stop it.
- **Port held by …**, on a Stopped sandbox whose port another program holds. Left out when Windows won't name the
  program, and when it's WSL's own relay (`wslrelay.exe`), which is a distro's port forwarded.
- **Update ready**, when the update check (below) found something.
- **Pinned to …**, when its ComfyUI is pinned to a release.

## Polling never wakes a distro

Home reads the sandboxes when it opens, after each of its own actions, and every 3 seconds (every half second while
an install's questions are up, to see when it has saved its sandbox). Reading never calls `wsl.exe` and never runs
anything inside a distro: running anything inside one starts it if it's stopped. It reads `installs.json` (under its
lock), looks at the run-window markers, asks Windows what listens on each finished sandbox's port and, only when
something does, makes one HTTP request to it (2-second timeout). The sandboxes are read all at once. Several homes
may be open at once, each polling on its own.

Opening a Stopped sandbox's menu calls `wsl.exe --list --running` once, to grey out Show logs. Listing the running
distros starts none. It's never polled.

The one thing home runs inside a distro on its own is the update check. The first time home finds a sandbox Running, it checks in the background,
once per session, what bare `update` would report as pending, and marks the row "update ready". It only checks a
Running sandbox, writes no transcript, and nothing waits for it. A check that fails or can't tell leaves no mark.
After an update home ran, the mark is dropped and the sandbox is checked again the next time it's Running.

The GPU meters come from one `nvidia-smi --loop=1` that home starts when it opens and stops when it closes.

## The run window

It is `run --distro <name>`, in-process: what `run` opens at a terminal, and what `start` and the desktop shortcuts
open. It holds the run-window marker, so a sandbox has at most one.

- A second window, opened while one is open, only opens the browser (its `run` exits 0). It reads the running
  ComfyUI's log once so it shows the same details, says the other window has it, and closes on its own a few
  seconds later.
- A question `run` asks (its port is taken) is asked in the window. Esc ends `run` as interrupted (130) before
  anything has started.
- "Closing this window stops ComfyUI." is shown only while it's true.
- Before each new launch, `run` checks isolation. A failed or inconclusive check warns and still
  launches, and its standing notice keeps the isolation warning visible alongside the stop notice
  until ComfyUI stops. The full log has the reasons and the command to run for details.
- Ctrl+C twice stops ComfyUI through `run`'s own cleanup (130). Once ComfyUI has stopped, however it stopped, the
  window closes on its own.
- After a failure the window stays open on `run`'s message and transcript path until Enter or Esc closes it.
