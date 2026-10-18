# comfyui-sandbox

Isolated ComfyUI setups on Windows, each in its own hardened WSL distro with Docker, managed through two front ends over one core: a scriptable CLI and a terminal UI.

## Language

### What you manage

**Sandbox**:
One isolated ComfyUI that comfyui-sandbox created and owns, living in its own WSL distro. It is the one user-facing noun for that thing.
_Avoid_: Instance, install (as a noun), distro (when you mean the managed thing rather than the WSL mechanism)

**Setup**:
The files comfyui-sandbox deploys into a sandbox to define how ComfyUI is built and run, stamped with the CLI version that deployed them.
_Avoid_: Sandbox files, assets (in user-facing text)

### Where you see it

**Home**:
The terminal UI's main screen: the list of sandboxes with their states, and the actions you can take on them.
_Avoid_: Dashboard, main menu

**Run window**:
The console window in which one sandbox's ComfyUI runs in the foreground; ComfyUI stops when it closes.
_Avoid_: Launcher window, ComfyUI window

**Run-window marker**:
The sign, held by an open run window, that a sandbox already has one; it is what keeps a sandbox to a single run window.
_Avoid_: Single-instance marker, lock, mutex (in prose)

### How the terminal UI reaches the world

**TUI gateway**:
Everything the terminal UI knows about sandboxes and can do to them, behind one interface; screens see nothing else.
_Avoid_: Backend (sounds like the core), API, service, client, proxy

**Real gateway**:
The gateway that answers from the actual machine and acts by running the CLI's own commands.
_Avoid_: Live gateway, production gateway

**Demo gateway**:
The gateway that answers from made-up sandboxes and fake progress, so the screens can be shown and redesigned with nothing installed.
_Avoid_: Fake gateway, mock, prototype

### Doing something

**Operation**:
One run of a command line on a sandbox's behalf, from its start to its result.
_Avoid_: Job, task, action (an action is the menu entry that starts an operation)

**Operation handle**:
The terminal UI's grip on a running operation: its live progress, a way to cancel it, and its final result.
_Avoid_: Future, promise, job handle

**Plan**:
The full list of steps a long operation announces before its first step runs, each with an expected duration.
_Avoid_: Schedule, pipeline

**Step**:
One named stage of an operation, shown as running, done or failed.
_Avoid_: Phase, stage (in prose), task

**Step table**:
How a long command lays out its steps in code: each a title, its expected seconds, a condition and the function that does it. The steps its situation selects are both its plan and what it runs.
_Avoid_: Step list, recipe
