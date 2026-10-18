# Runs as the LINUX USER. Waits until ComfyUI answers on COMFY_PORT, so the first
# install (10-30 min) or a restart is one call. While waiting it prints one short
# progress line every INTERVAL seconds and at each phase change, e.g.
#   [07:30] [###---] 3/6 PyTorch (largest download) | data 3.1G | Collecting torch...
# The phases are a rough guide read from the log, not a precise percentage; the
# growing data size shows the download is moving. It is one long wsl.exe
# session, so it also keeps the distro from idling out while it runs.
# The entrypoint's "[setup] Previous install did not finish / is broken" lines are
# printed as soon as they show up in the log of this run.
# At the end it prints a short report (container state, the last log lines)
# and exits with:
#   0  ready (HTTP 200)
#   1  the container stopped: the install or ComfyUI failed (the container has
#      no restart policy, so a failed run stays stopped). The log lines shown
#      are the end of the failed run; "not retrying" in them means entrypoint.sh
#      refused to try again (repeated failed installs, or ./data not writable).
#   2  not ready after TIMEOUT_MIN; run it again while the log keeps moving
# Env: TIMEOUT_MIN  default 25
#      INTERVAL     seconds between progress lines (default 60)
#      TAIL         log lines shown when ready or timed out (default 15)
#      PROJECT_DIR  default ~/comfyui-docker
# Everything goes to stdout.

# The CLI turns on errexit and pipefail. A watcher must survive a failed probe
# (du on a file that is being replaced, grep with no match, Docker still
# starting), so turn them off here.
set +e +o pipefail
C=comfyui
TIMEOUT_MIN=${TIMEOUT_MIN:-25}
INTERVAL=${INTERVAL:-60}
TAIL=${TAIL:-15}
case "$TIMEOUT_MIN$INTERVAL$TAIL" in *[!0-9]*) echo "[error] TIMEOUT_MIN, INTERVAL and TAIL must be whole numbers"; exit 1;; esac
dir=${PROJECT_DIR:-$HOME/comfyui-docker}
cd "$dir" 2>/dev/null || { echo "[error] no $dir: ComfyUI is not deployed in this distro"; exit 1; }
# Windows-side port from .env, read like the CLI's .env parser: the last assignment wins,
# an `export ` prefix and quotes are allowed, a trailing `# comment` is ignored
# (installs without COMFY_PORT use 8188).
port=$(sed -n 's/\r$//; s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}COMFY_PORT=[[:space:]]*//p' .env 2>/dev/null | tail -n 1 || true)
case "$port" in
  \"*) port=${port#\"}; port=${port%%\"*};;
  \'*) port=${port#\'}; port=${port%%\'*};;
  *) port=${port%%[[:space:]]#*}; port=${port%"${port##*[![:space:]]}"};;
esac
port=${port:-8188}

insp() { docker inspect -f "$1" "$C" 2>/dev/null; }
# $1 = log lines
report() {
  echo "container: $(insp '{{.State.Status}}') (exit code $(insp '{{.State.ExitCode}}')), started $(insp '{{.State.StartedAt}}')"
  echo "----- last log lines -----"
  # Colour codes and long lines only cost context.
  docker logs --tail "$1" "$C" 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-300
  echo "--------------------------"
}

phase_names=("waiting for the log" "comfy-cli" "ComfyUI + Manager" \
  "PyTorch (largest download)" "dependencies" "starting ComfyUI" "ready")

[ -n "$(insp '{{.State.Status}}')" ] || { echo "[error] no '$C' container: ComfyUI is not deployed in this distro"; exit 1; }
noted=""
start=$SECONDS; deadline=$((SECONDS + TIMEOUT_MIN * 60)); last_emit=0; last_phase=-1
while :; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$port/")
  if [ "$code" = 200 ]; then
    echo "[ok] ComfyUI answers (HTTP 200) after $(((SECONDS - start) / 60)) min of waiting"
    report "$TAIL"; exit 0
  fi
  # No restart policy: a failed install or ComfyUI run leaves the container
  # exited (so do a docker compose stop and closing the Start window).
  status=$(insp '{{.State.Status}}')
  if [ "$status" != running ]; then
    echo "[error] the container is ${status:-gone}, not running: the install or ComfyUI failed, or it was stopped. Last log lines:"
    report 40; exit 1
  fi

  # Logs of the current run only, so a new start begins the phases again.
  # Progress bars (\r) and colour codes would garble the latest line.
  started=$(insp '{{.State.StartedAt}}')
  logs=$(docker logs --since "$started" "$C" 2>&1 | tr '\r' '\n' | sed 's/\x1b\[[0-9;]*m//g')

  for note in "Previous install did not finish" "Previous install is broken"; do
    case "$noted" in *"|$note"*) continue;; esac
    line=$(printf '%s\n' "$logs" | grep -F "[setup] $note" | head -n 1)
    if [ -n "$line" ]; then echo "$line"; noted="$noted|$note"; fi
  done

  phase=$(printf '%s\n' "$logs" | awk '
    /To see the GUI go to/                         {p=6; next}
    /Starting server|Total VRAM|pytorch version:/ {if (p<5) p=5; next}
    /\[setup\] Installing ComfyUI/                 {if (p<2) p=2; next}
    /\[setup\] Creating venv/                      {if (p<1) p=1; next}
    p==2 && tolower($0) ~ /torch/                  {p=3; next}
    p==3 && tolower($0) ~ /requirements|manager/   {p=4; next}
    END {print p+0}')
  case "$phase" in [0-6]) ;; *) phase=0;; esac

  el=$((SECONDS - start))
  if [ "$phase" != "$last_phase" ] || [ $((el - last_emit)) -ge "$INTERVAL" ]; then
    bar=$(printf '%*s' "$phase" '' | tr ' ' '#')$(printf '%*s' $((6 - phase)) '' | tr ' ' '-')
    size=$(du -sh data 2>/dev/null | cut -f1)
    last=$(printf '%s\n' "$logs" | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-100)
    printf '[%02d:%02d] [%s] %d/6 %s | data %s | %s\n' $((el / 60)) $((el % 60)) \
      "$bar" "$phase" "${phase_names[$phase]}" "${size:-?}" "${last:-(no log yet)}"
    last_emit=$el; last_phase=$phase
  fi

  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[timeout] not ready after $TIMEOUT_MIN min (HTTP ${code:-none}, phase $phase/6). If the log below moved on since the last run, run this again."
    report "$TAIL"; exit 2
  fi
  sleep 10
done
