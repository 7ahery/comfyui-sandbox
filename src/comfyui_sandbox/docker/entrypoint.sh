#!/usr/bin/env bash
# -------------------------------------------------------------------
# NOTE: This file is copied into the Docker image at build time.
# After editing it, rebuild for changes to take effect:
#     cd ~/comfyui-docker && docker compose up -d --build
# A plain "docker compose restart" will still run the OLD version.
# -------------------------------------------------------------------
set -e
WS=/workspace/ComfyUI

# The container has no restart policy (docker-compose.yml): any error below
# exits, the container stays stopped, and the error is the end of the log.
# Errors a retry can't fix say "not retrying".

# 0. Fail clearly on permission errors
if [ ! -w /workspace ]; then
  echo "[error] /workspace (~/comfyui-docker/data) is not writable by uid $(id -u); not retrying." >&2
  echo "[error] In a WSL root shell (wsl -d <distro> -u root) run: chown -R $(id -u):$(id -g) ~<user>/comfyui-docker/data" >&2
  echo "[error] Then start ComfyUI again (Start shortcut, or docker compose up -d in ~/comfyui-docker)." >&2
  exit 1
fi
mkdir -p "$HOME"

# 1. Stop re-running a first install that keeps failing (bad --cuda-version,
#    no network): each start would download gigabytes only to fail again.
#    Each failure appends the install flags to FAILS; after MAX_FAILS failures
#    with the same flags, exit at once instead. New flags in .env start a
#    fresh count. A cut-off install (container stopped, WSL shut down) never
#    reaches the counter, so it doesn't count. Such a cut-off install is redone
#    from the start, but its downloads (pip/uv caches, PIP_CACHE_DIR and
#    UV_CACHE_DIR in the Dockerfile) persist in data/, so it doesn't fetch them again.
DONE=/workspace/.comfyui-install-complete
FAILS=/workspace/.comfyui-install-failures
MAX_FAILS=3
INSTALL_ARGS=${COMFY_INSTALL_ARGS:-"--nvidia --version latest"}
install_failed() {
  echo "$INSTALL_ARGS" >> "$FAILS"
  echo "[error] $1 failed ($(grep -cxF -- "$INSTALL_ARGS" "$FAILS") of $MAX_FAILS tries with: $INSTALL_ARGS)" >&2
  exit 1
}
if [ ! -f "$DONE" ]; then
  fails=$(grep -cxF -- "$INSTALL_ARGS" "$FAILS" 2>/dev/null || true)
  if [ "${fails:-0}" -ge "$MAX_FAILS" ]; then
    echo "[error] The install failed $fails times with: $INSTALL_ARGS; not retrying." >&2
    echo "[error] The cause is in the log above. Once fixed, on the WSL host in ~/comfyui-docker:" >&2
    echo "[error]   changed COMFY_INSTALL_ARGS in .env:  docker compose up -d" >&2
    echo "[error]   anything else:  rm data/.comfyui-install-failures && docker compose up -d" >&2
    exit 1
  fi
fi

# 2. Persistent venv + comfy-cli (first run, or a first run that was cut off)
if [ ! -x /workspace/venv/bin/comfy ]; then
  echo "[setup] Creating venv and installing comfy-cli..."
  [ -x /workspace/venv/bin/python ] || /usr/local/bin/python -m venv /workspace/venv
  { pip install --upgrade pip && pip install comfy-cli; } || install_failed "Installing comfy-cli"
fi

# 3. Tracking off (belt and braces on top of DO_NOT_TRACK)
comfy tracking disable >/dev/null 2>&1 || true

# 4. Install ComfyUI + Manager (flags from COMFY_INSTALL_ARGS in .env). The
#    marker is written only after the install succeeds, so an install that was
#    cut off (sleep, network drop, container stopped) is redone on the next start.
if [ ! -f "$DONE" ]; then
  if [ -d "$WS/user" ] && ! compgen -G "$WS.broken-*" >/dev/null; then
    # A user/ folder (e.g. a backup restored before the install finished) may
    # hold workflows: keep it. Only one copy is ever kept, so this can't fill
    # the disk.
    aside="$WS.broken-$(date +%Y%m%d-%H%M%S)"
    echo "[setup] Previous install is broken; moving it to $aside (check it, then delete it)"
    mv "$WS" "$aside"
  elif [ -e "$WS" ]; then
    # ComfyUI never started from it, so nothing of yours is in it. Deleting it
    # (not moving it aside) stops repeated failed installs from filling the
    # disk.
    echo "[setup] Previous install did not finish; removing it and starting over"
    rm -rf "$WS"
  fi
  echo "[setup] Installing ComfyUI via comfy-cli: install $INSTALL_ARGS"
  # shellcheck disable=SC2086  # INSTALL_ARGS is a list of flags
  comfy --skip-prompt --workspace "$WS" install $INSTALL_ARGS || install_failed "Installing ComfyUI"
  touch "$DONE"
  rm -f "$FAILS"
fi

# 5. Keep the Manager package matched to the ComfyUI version
if [ -f "$WS/manager_requirements.txt" ]; then
  pip install -q -r "$WS/manager_requirements.txt" || echo "[warn] Manager requirements sync failed"
fi

comfy --skip-prompt set-default "$WS" >/dev/null 2>&1 || true

# 6. Start ComfyUI with Manager enabled; models come from /models
cd "$WS"
exec python main.py --listen 0.0.0.0 --port 8188 --models-directory /models ${COMFY_ARGS}
