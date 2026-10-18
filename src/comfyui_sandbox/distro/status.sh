# Runs as the LINUX USER. Quick, non-blocking, read-only status snapshot for `status`.
# Prints `container=<docker state>` (`missing` when there is no container), then, when there
# is one, a marker line and the last log lines (TAIL, default 25).
cd "${PROJECT_DIR:-$HOME/comfyui-docker}" 2>/dev/null || { echo "container=missing"; exit 0; }
state=$(docker inspect -f '{{.State.Status}}' comfyui 2>/dev/null || true)
echo "container=${state:-missing}"
[ -n "$state" ] || exit 0
echo "----- last log lines -----"
docker compose logs --no-color --no-log-prefix --tail "${TAIL:-25}" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-300 || true
