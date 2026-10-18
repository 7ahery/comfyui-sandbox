"""A run window closes as soon as ComfyUI is stopped on purpose (`stop`, home's Stop, a command that
ends stopped), rather than waiting out watch.sh's restart grace, during which its marker made home
show the sandbox as starting. A restart (no stop asked for) is still waited out.

watch.sh runs here against fake docker and sleep, and the stop's own argv runs while it watches."""

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import comfyui_sandbox
from comfyui_sandbox import steps
from toolchain import bash

WATCH = Path(comfyui_sandbox.__file__).parent / "distro" / "watch.sh"

# `docker wait` blocks until `docker compose stop` has run, then says 143; once it has, the
# container is "exited". `sleep` only records that it was called.
DOCKER = """#!/bin/sh
case "$1" in
  wait) touch "$STATE/waiting"; while [ ! -e "$STATE/stopped" ]; do :; done; echo 143;;
  inspect) echo exited;;
  compose) [ "$2" = stop ] && touch "$STATE/stopped";;
esac
exit 0
"""


def machine(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """A project folder, the env for fake docker and sleep, and the folder they keep their state in."""
    project, bin_dir, state = tmp_path / "project", tmp_path / "bin", tmp_path / "state"
    for folder in (project, bin_dir, state):
        folder.mkdir()
    (bin_dir / "docker").write_text(DOCKER, newline="\n")
    (bin_dir / "sleep").write_text('#!/bin/sh\necho "$@" >> "$STATE/slept"\n', newline="\n")
    env = {
        "PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin",
        "PROJECT_DIR": project.as_posix(),
        "TAIL": "1",
        "STATE": state.as_posix(),
    }
    return project, env, state


def command(argv: list[str]) -> list[str]:
    return [bash(), "-c", 'exec "$@"', "bash", *argv]


def watch_through(env: dict[str, str], state: Path, stop: list[str]) -> subprocess.CompletedProcess:
    """watch.sh, with `stop` run once it is waiting on the container."""
    watch = subprocess.Popen(command([str(WATCH)]), env=env, stdout=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60
        while not (state / "waiting").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        stopped = subprocess.run(command(stop), env=env, capture_output=True, text=True, timeout=60)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        out, _ = watch.communicate(timeout=60)
    finally:
        watch.kill()
    return subprocess.CompletedProcess(watch.args, watch.returncode, out)


def test_a_stop_asked_for_ends_the_watch_at_once(tmp_path):
    project, env, state = machine(tmp_path)
    stop = steps.stop_argv(SimpleNamespace(distro="comfy-docker", linux_user="comfy"))
    stop[stop.index("--chdir") + 1] = project.as_posix()

    watched = watch_through(env, state, stop)

    assert watched.returncode == 0, watched.stdout
    assert "[ok] ComfyUI stopped" in watched.stdout
    assert not (state / "slept").exists()  # no wait for a restart


def test_a_stop_not_asked_for_is_still_waited_on_as_a_maybe_restart(tmp_path):
    project, env, state = machine(tmp_path)
    (project / steps.STOP_ASKED).touch()  # an earlier stop's, from before this window

    watched = watch_through(env, state, ["env", "--chdir", project.as_posix(), "docker", "compose", "stop"])

    assert watched.returncode == 0, watched.stdout
    assert (state / "slept").exists()  # it gave the container time to come back
