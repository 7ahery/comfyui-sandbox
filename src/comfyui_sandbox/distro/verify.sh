# comfyui-sandbox verify: the security and health checklist. Runs as ROOT inside the distro, read-only.
# Env: GPU_MODE (nvidia|cpu, default nvidia), MODELS_DIR (default /mnt/models).
# ISOLATION_ONLY=1 stops after the WSL isolation section, for the advisory startup check.
# Output, one line each: "== Section ==", "[PASS|WARN|FAIL|INFO] text", and after a WARN or FAIL
# an optional "[HINT] what to do". Exit 0 when nothing FAILed, 1 otherwise. The Windows-side reach
# check is not here (it runs on Windows); the CLI adds it.
set +e +u +o pipefail
GPU_MODE=${GPU_MODE:-nvidia}; MODELS_DIR=${MODELS_DIR:-/mnt/models}
rc=0
hint(){ [ -n "$1" ] && echo "[HINT] $1"; return 0; }
pass(){ echo "[PASS] $1"; }
warn(){ echo "[WARN] $1"; hint "$2"; }
fail(){ echo "[FAIL] $1"; hint "$2"; rc=1; }

echo "== WSL isolation =="
if targets=$(findmnt -rn -o TARGET) && [ -n "$targets" ]; then
  drives=$(printf '%s\n' "$targets" | grep -E '^/mnt/[a-z]$')
  if [ -z "$drives" ]; then pass "no Windows drive letters mounted"
  else fail "drive mounts present: $drives" "Run 'comfyui-sandbox install' again to re-apply the hardening, then restart the distro."; fi
else
  fail "Windows drive mounts could not be checked" "Run 'comfyui-sandbox verify' again and check that findmnt can list mounts."
fi
if sed -n '/^\[automount\]/,/^\[/p' /etc/wsl.conf | grep -qiE '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*false'; then
  pass "automount disabled in /etc/wsl.conf"
else fail "automount not disabled" "Run 'comfyui-sandbox install' again to rewrite /etc/wsl.conf, then restart the distro."; fi
# Check the persisted settings independently of the runtime handler. Reject duplicate
# sections/keys and malformed lines instead of guessing how WSL will interpret them.
if awk '
  { sub(/\r$/, ""); gsub(/^[ \t]+|[ \t]+$/, "") }
  /^[#;]/ || /^$/ { next }
  # WSL supports quoted/multiline values and line continuations. Managed config
  # needs none of them. Reject them anywhere, so a preceding value cannot swallow
  # an interop section that this conservative parser would otherwise accept.
  index($0, "\\") || index($0, "\"") || index($0, sprintf("%c", 39)) { bad = 1; next }
  /^\[[^][]+\]$/ {
    section = tolower($0)
    if (section == "[interop]") sections++
    next
  }
  !/^[^=]+=/ { bad = 1; next }
  section == "[interop]" {
    split($0, parts, "=")
    key = tolower(parts[1]); gsub(/^[ \t]+|[ \t]+$/, "", key)
    value = tolower(parts[2]); gsub(/^[ \t]+|[ \t]+$/, "", value)
    if (key == "enabled" || key == "appendwindowspath") {
      seen[key]++
      if (value != "false" || split($0, fields, "=") != 2) bad = 1
    }
  }
  END { exit (bad || sections != 1 || seen["enabled"] != 1 || seen["appendwindowspath"] != 1) }
' /etc/wsl.conf 2>/dev/null; then
  pass "WSL interop disabled in /etc/wsl.conf"
else
  fail "WSL interop settings are missing, invalid or not disabled" \
    "Run 'comfyui-sandbox install' again to rewrite /etc/wsl.conf, then restart the distro."
fi

# An enabled handler may be shared with another distro. It does not prove that this
# distro has an interop server. Do not mutate it or mistake a failed/missing-path
# execution probe for isolation. Until a validated probe exists, fail inconclusive.
runtime=unknown
if IFS= read -r status < /proc/sys/fs/binfmt_misc/status; then
  case "$status" in
    enabled|disabled)
      # WSL can shadow the global status file. Inspect the entries even when it
      # says disabled, and distinguish failed enumeration from an empty registry.
      if entries=$(find /proc/sys/fs/binfmt_misc -maxdepth 1 -name 'WSLInterop*' -print); then
        runtime=inactive
        while IFS= read -r entry; do
          [ -n "$entry" ] || continue
          if IFS= read -r status < "$entry" && [ "$status" = disabled ]; then continue; fi
          runtime=unknown
          break
        done <<< "$entries"
      fi
      ;;
  esac
fi 2>/dev/null
if [ "$runtime" = inactive ]; then
  pass "WSL Windows-binary handler inactive"
else
  fail "WSL interop runtime isolation could not be verified" \
    "Keep this sandbox stopped until Windows execution is confirmed blocked. Do not disable the shared binfmt handler, it can affect other distros."
fi
while read -r tgt src; do
  case "$tgt" in /mnt/models|/usr/lib/wsl/*|/mnt/wsl*|/tmp/.X11-unix) ;;
    *) warn "unexpected Windows-backed mount: $tgt <- $src" "Check that nothing else mounts a Windows folder into the distro.";; esac
done < <(findmnt -rn -t 9p,drvfs,virtiofs -o TARGET,SOURCE)
if [ "${ISOLATION_ONLY:-0}" = 1 ]; then
  echo "[INFO] WSL isolation check complete"
  exit "$rc"
fi

if [ "$MODELS_DIR" = /mnt/models ]; then
  if mountpoint -q /mnt/models; then
    pass "/mnt/models mounted from Windows ($(ls -A /mnt/models | wc -l) entries)"
    if findmnt -n -o OPTIONS /mnt/models | grep -q noexec; then pass "/mnt/models is noexec"
    else warn "/mnt/models not noexec" "Run 'comfyui-sandbox install' again to rewrite the mount in /etc/fstab."; fi
  else fail "/mnt/models is not mounted" "Check that the Windows models folder still exists, then restart the distro (wsl --terminate)."; fi
elif [ -d "$MODELS_DIR" ]; then pass "models folder inside WSL: $MODELS_DIR ($(ls -A "$MODELS_DIR" | wc -l) entries)"
else fail "$MODELS_DIR missing" "Create it, or run 'comfyui-sandbox install' again."; fi

echo "== Resources =="
echo "[INFO] WSL sees $(free -g | awk '/^Mem:/{print $2}') GB RAM, $(free -g | awk '/^Swap:/{print $2}') GB swap, $(nproc) CPUs"

echo "== Docker =="
if systemctl is-active --quiet docker; then pass "docker service active"
else fail "docker service not active" "Start it with 'systemctl start docker', or run 'comfyui-sandbox install' again."; fi
if [ "$GPU_MODE" = nvidia ]; then
  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then pass "nvidia runtime registered"
  else fail "nvidia runtime missing" "Run 'comfyui-sandbox install' again to reinstall the NVIDIA Container Toolkit."; fi
else
  echo "[INFO] CPU-only install: GPU checks skipped"
fi
if grep -qs '"no-new-privileges": true' /etc/docker/daemon.json; then pass "daemon default no-new-privileges"
else warn "daemon.json lacks no-new-privileges" "Run 'comfyui-sandbox install' again to rewrite /etc/docker/daemon.json."; fi

echo "== ComfyUI container =="
if [ "$(docker inspect -f '{{.State.Running}}' comfyui 2>/dev/null)" = true ]; then
  pass "container running"
  # Container port 8188 is published on COMFY_PORT; read it back from Docker.
  ports=$(docker port comfyui 8188 2>/dev/null)
  port=$(echo "$ports" | head -n1 | sed 's/.*://')
  if [ -z "$ports" ]; then fail "port 8188 is not published" "Redeploy the container: 'comfyui-sandbox rebuild'."
  elif echo "$ports" | grep -qvE '^(127\.0\.0\.1|\[::1\]):'; then
    fail "port published beyond localhost: $(echo $ports)" "Redeploy the container: 'comfyui-sandbox rebuild'."
  else pass "port $port bound to localhost only ($(echo $ports))"; fi
  if ! u=$(docker exec comfyui id -u 2>&1); then fail "could not check container uid: $u" "Is the container healthy? See 'comfyui-sandbox logs'."
  elif [ "$u" = 0 ]; then fail "container runs as root" "Redeploy the container: 'comfyui-sandbox rebuild'."
  else pass "runs as non-root uid $u"; fi
  if [ "$(docker inspect -f '{{.HostConfig.Privileged}}' comfyui)" = false ]; then pass "not privileged"
  else fail "container is privileged" "Redeploy the container: 'comfyui-sandbox rebuild'."; fi
  policy=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' comfyui)
  case "$policy" in no|"") pass "no restart policy (starts only from the Start shortcut or docker compose up)";;
    *) warn "restart policy '$policy': ComfyUI starts whenever the distro boots" "Redeploy the container: 'comfyui-sandbox rebuild'.";; esac
  if docker inspect -f '{{.HostConfig.CapDrop}}' comfyui | grep -q ALL; then pass "all capabilities dropped"
  else fail "capabilities not dropped" "Redeploy the container: 'comfyui-sandbox rebuild'."; fi
  if docker inspect -f '{{.HostConfig.SecurityOpt}}' comfyui | grep -q no-new-privileges; then pass "no-new-privileges set"
  else fail "no-new-privileges missing" "Redeploy the container: 'comfyui-sandbox rebuild'."; fi
  mounts=$(docker inspect -f '{{range .Mounts}}{{.Source}}->{{.Destination}} {{end}}' comfyui)
  if echo "$mounts" | grep -q docker.sock; then fail "docker.sock mounted into container" "Redeploy the container: 'comfyui-sandbox rebuild'."
  else pass "mounts: $mounts"; fi
  if [ "$GPU_MODE" = nvidia ]; then
    gpu=$(docker exec comfyui python -c 'import torch;print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")' 2>/dev/null)
    case "$gpu" in True*) pass "PyTorch sees GPU: ${gpu#True }";;
      "") warn "torch not importable yet" "The first start may still be installing PyTorch; run verify again in a few minutes.";;
      *) fail "PyTorch cannot see the GPU" "Check 'nvidia-smi' on Windows, then run 'comfyui-sandbox install' again.";; esac
  else
    warn "CPU-only mode: generation will be slow"
  fi
  echo "[INFO] COMFY_ARGS: $(docker exec comfyui printenv COMFY_ARGS 2>/dev/null)"
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port:-8188}/")
  if [ "$code" = 200 ]; then pass "ComfyUI answers on 127.0.0.1:${port:-8188} (inside the distro)"
  else warn "ComfyUI HTTP status: ${code:-none}" "It may still be starting; see 'comfyui-sandbox logs'."; fi
else
  fail "container 'comfyui' is not running" "Start it with 'comfyui-sandbox start', or see 'comfyui-sandbox logs'."
fi
exit $rc
