# Runs as ROOT inside the distro, BEFORE deploying ComfyUI. Idempotent.
# Creates the project folder and its data/ folder with the right owner, so the
# container user (same UID/GID as the Linux user) can write to it and Docker
# never creates it as root.
# Required env: LINUX_USER
# Optional env: PROJECT_DIR (default /home/<user>/comfyui-docker)
: "${LINUX_USER:?LINUX_USER is required}"
id "$LINUX_USER" >/dev/null 2>&1 || { echo "[error] user '$LINUX_USER' does not exist" >&2; exit 1; }
home=$(getent passwd "$LINUX_USER" | cut -d: -f6)
PROJECT_DIR=${PROJECT_DIR:-$home/comfyui-docker}
uid=$(id -u "$LINUX_USER"); gid=$(id -g "$LINUX_USER")

mkdir -p "$PROJECT_DIR/data"
chown "$uid:$gid" "$PROJECT_DIR"
chown -R "$uid:$gid" "$PROJECT_DIR/data"
chmod 755 "$PROJECT_DIR"
chmod 750 "$PROJECT_DIR/data"
echo "[ok] $PROJECT_DIR/data ready: $(stat -c '%U:%G %a' "$PROJECT_DIR/data")"
