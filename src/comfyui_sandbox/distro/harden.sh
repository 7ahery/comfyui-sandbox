# Runs as ROOT inside the distro (streamed by the CLI). Idempotent.
# Required env: LINUX_USER, MODELS_MODE (windows|wsl)
#   windows: MODELS_WIN (e.g. C:\AI\Models) -> mounted at /mnt/models
#   wsl:     MODELS_DIR (e.g. /home/<user>/comfyui-models) -> plain ext4 folder
# Effect after the next `wsl --terminate`: no Windows drives, no interop.
: "${LINUX_USER:?}" "${MODELS_MODE:?}"
MNT=/mnt/models
id "$LINUX_USER" >/dev/null 2>&1 || { echo "[error] user '$LINUX_USER' does not exist" >&2; exit 1; }
uid=$(id -u "$LINUX_USER"); gid=$(id -g "$LINUX_USER")

# --- /etc/wsl.conf ---------------------------------------------------------
[ -f /etc/wsl.conf ] && [ ! -f /etc/wsl.conf.orig ] && cp /etc/wsl.conf /etc/wsl.conf.orig
cat > /etc/wsl.conf <<CONF
# Managed by comfyui-sandbox. Original kept in /etc/wsl.conf.orig
[boot]
systemd = true

[automount]
# No C:, D:, ... inside Linux. Only /etc/fstab entries are mounted.
enabled = false
mountFsTab = true

[interop]
# Linux processes cannot start Windows programs.
enabled = false
appendWindowsPath = false

[user]
default = ${LINUX_USER}
CONF
echo "[ok] /etc/wsl.conf written"

touch /etc/fstab
sed -i "\#[[:space:]]${MNT}[[:space:]]#d" /etc/fstab   # drop any old models line

case "$MODELS_MODE" in
windows)
  : "${MODELS_WIN:?MODELS_WIN is required in windows mode}"
  win=${MODELS_WIN//\//\\}; win=${win%\\}
  src=$(printf '%s' "$win" | sed -e 's/\\/\\134/g' -e 's/ /\\040/g')   # fstab escapes
  printf '%s %s drvfs rw,noatime,nosuid,nodev,noexec,uid=%s,gid=%s,umask=022 0 0\n' \
    "$src" "$MNT" "$uid" "$gid" >> /etc/fstab
  mkdir -p "$MNT"
  echo "[ok] fstab: $(grep "[[:space:]]${MNT}[[:space:]]" /etc/fstab)"
  mountpoint -q "$MNT" || mount "$MNT" || { echo "[error] mounting $MODELS_WIN failed" >&2; exit 1; }
  echo "[ok] $MNT mounted ($(ls -A "$MNT" | wc -l) entries):"; ls "$MNT" | head -n 20
  ;;
wsl)
  : "${MODELS_DIR:?MODELS_DIR is required in wsl mode}"
  # Same rule as deploy_comfyui.sh: the path goes into .env and a compose volume.
  printf '%s' "$MODELS_DIR" | grep -qE '^/[A-Za-z0-9._/ -]+$' \
    || { echo "[error] MODELS_DIR must be an absolute path of letters, digits, space, . _ - / only: $MODELS_DIR" >&2; exit 1; }
  # Keep models outside the project folder, so a rebuild or a fresh recreate
  # (rm -rf data) never deletes them. Same rule as deploy_comfyui.sh.
  home=$(getent passwd "$LINUX_USER" | cut -d: -f6)
  m=$(realpath -m "$MODELS_DIR")
  case "$m/" in
    "$home/comfyui-docker/"*|//|/home/|"$home/")
      echo "[error] MODELS_DIR must be its own folder outside $home/comfyui-docker (e.g. $home/comfyui-models), so recreating ComfyUI can't delete it: $MODELS_DIR" >&2; exit 1;;
  esac
  mountpoint -q "$MNT" && umount "$MNT" || true
  mkdir -p "$MODELS_DIR"
  # Core ComfyUI folders now; models_structure.sh adds the exact
  # set from the installed ComfyUI version later.
  for d in checkpoints loras vae text_encoders clip clip_vision diffusion_models unet \
           controlnet upscale_models embeddings vae_approx style_models hypernetworks \
           gligen photomaker diffusers configs model_patches audio_encoders; do
    mkdir -p "$MODELS_DIR/$d"
  done
  chown -R "$uid:$gid" "$MODELS_DIR"
  echo "[ok] models folder inside WSL: $MODELS_DIR"; ls "$MODELS_DIR"
  ;;
*) echo "[error] MODELS_MODE must be windows or wsl" >&2; exit 1;;
esac
echo "[next] Restart the distro from Windows: wsl.exe --terminate <distro>"
