# Runs as the LINUX USER. Holds until the ComfyUI container stops, then says why (`run`'s window).
# One long wsl.exe session; it blocks in `docker wait`. A container that is stopped and started
# again within ~60 s (docker compose restart / up -d by another command) is waited on again,
# so a restart does not end the run window. A stop comfyui-sandbox asked for (`stop`, a command that
# ends stopped) leaves .stop-asked in the project folder first (steps.stop_argv): that one isn't
# waited on, so the window closes with the container.
# Exits with:
#   0  the container stopped cleanly (exit code 0, or 143 = stopped by `docker compose stop`),
#      or a stop was asked for
#   3  the container stopped on its own (any other exit code, e.g. 137 = out of memory, or it is gone):
#      the exit code and the last log lines are printed
# Env: PROJECT_DIR  default ~/comfyui-docker
#      TAIL         log lines shown after a crash (default 25)
# Everything goes to stdout.

set +e +o pipefail
C=comfyui
TAIL=${TAIL:-25}
case "$TAIL" in ''|*[!0-9]*) echo "[error] TAIL must be a whole number"; exit 1;; esac
dir=${PROJECT_DIR:-$HOME/comfyui-docker}
cd "$dir" 2>/dev/null || { echo "[error] no $dir: ComfyUI is not deployed in this distro"; exit 1; }

state() { docker inspect -f '{{.State.Status}}' "$C" 2>/dev/null; }

asked=.stop-asked
rm -f "$asked"  # an earlier stop's, from before this window
while :; do
  code=$(docker wait "$C" 2>/dev/null)
  if [ -e "$asked" ]; then
    echo "[ok] ComfyUI stopped (container exit code ${code:-unknown})"
    exit 0
  fi
  # Give a restart time to bring the container back.
  for _ in $(seq 30); do
    [ "$(state)" = running ] && break
    sleep 2
  done
  [ "$(state)" = running ] && continue
  break
done

code=${code:-unknown}
case "$code" in
  0|143) echo "[ok] ComfyUI stopped (container exit code $code)"; exit 0;;
esac
echo "[error] ComfyUI stopped (container exit code $code). Last log lines:"
docker compose logs --no-color --no-log-prefix --tail "$TAIL" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-300
exit 3
