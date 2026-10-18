# Runs as the LINUX USER (not root) inside the distro.
# Required env: PAYLOAD  base64 tar of the docker assets plus the .env the CLI wrote from
#                        the install answers (see comfyui_sandbox.payload and .envfile)
# Optional env:
#   PROJECT_DIR  default ~/comfyui-docker
#   MODELS_DIR   the models folder the .env points at (default /mnt/models, the Windows
#                folder mounted by harden.sh); checked here, not written
#   ENV_BACKUP_STAMP  the stamp of the .env backup (default: this script's local-time stamp)
#   BACKUP_ENV   "no" skips the .env backup (the caller found nothing to change in it)
# The asset files are deployed unchanged. Never deletes ./data. Existing files are backed
# up (as <name>.bak-<timestamp>) before they are overwritten. Returns once the container
# is started, before ComfyUI is ready: wait_ready.sh waits for that.
: "${PAYLOAD:?PAYLOAD is required}"
PROJECT_DIR=${PROJECT_DIR:-$HOME/comfyui-docker}
MODELS_DIR=${MODELS_DIR:-/mnt/models}
# MODELS_DIR ends up in a compose volume line: no $, #, :, quotes etc.
printf '%s' "$MODELS_DIR" | grep -qE '^/[A-Za-z0-9._/ -]+$' \
  || { echo "[error] MODELS_DIR must be an absolute path of letters, digits, space, . _ - / only: $MODELS_DIR" >&2; exit 1; }
# Models must live outside the project folder, so a rebuild or a fresh recreate
# (rm -rf data) never deletes them. Same rule as harden.sh.
models_real=$(realpath -m "$MODELS_DIR"); project_real=$(realpath -m "$PROJECT_DIR")
case "$models_real/" in
  "$project_real/"*|//|/home/|"$(realpath -m "$HOME")/")
    echo "[error] MODELS_DIR must be its own folder outside $PROJECT_DIR (e.g. $HOME/comfyui-models), so recreating ComfyUI can't delete it: $MODELS_DIR" >&2; exit 1;;
esac

[ "$(id -u)" = 0 ] && { echo "[error] run as the normal user, not root" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "[error] cannot reach Docker as $(id -un) (docker group? restart the distro)" >&2; exit 1; }
if [ "$MODELS_DIR" = /mnt/models ]; then
  mountpoint -q /mnt/models || { echo "[error] /mnt/models is not mounted" >&2; exit 1; }
else
  [ -d "$MODELS_DIR" ] && [ -w "$MODELS_DIR" ] || { echo "[error] $MODELS_DIR missing or not writable" >&2; exit 1; }
fi

# data/ is prepared by prepare_data.sh (as root); make sure it is usable.
[ -d "$PROJECT_DIR/data" ] && [ -w "$PROJECT_DIR/data" ] && [ "$(stat -c %u "$PROJECT_DIR/data")" = "$(id -u)" ] \
  || { echo "[error] $PROJECT_DIR/data must exist and be owned by $(id -un): the data folder step did not run" >&2; exit 1; }

mkdir -p "$PROJECT_DIR"; cd "$PROJECT_DIR" || exit 1
ts=$(date +%Y%m%d-%H%M%S)
for f in Dockerfile docker-compose.yml docker-compose.gpu.yml entrypoint.sh .env; do
  stamp=$ts
  if [ "$f" = .env ]; then
    [ "${BACKUP_ENV:-}" = no ] && continue
    stamp=${ENV_BACKUP_STAMP:-$ts}
  fi
  if [ -f "$f" ]; then
    cp -p "$f" "$f.bak-$stamp"
    echo "[info] backed up $f -> $f.bak-$stamp"
  fi
done
printf '%s' "$PAYLOAD" | base64 -d | tar -xf - --no-same-owner
sed -i 's/\r$//' Dockerfile docker-compose.yml docker-compose.gpu.yml entrypoint.sh .env
chmod 755 entrypoint.sh
echo "[ok] .env:"; grep -v '^#' .env
docker compose config -q || { echo "[error] compose config is invalid" >&2; exit 1; }
echo "[ok] project files in $PROJECT_DIR:"; ls -la

docker compose build --pull
docker compose up -d
echo "[ok] container started. First start installs PyTorch + ComfyUI (several GB, 10-30 min)."
