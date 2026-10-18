# Runs as the LINUX USER. Replaces the project's .env with ENV_B64, keeping the old one.
# Env: ENV_B64     base64 of the whole new file (LF endings), from comfyui_sandbox.envfile.merge
#      STAMP        suffix of the backup: .env.bak-<STAMP>
#      PROJECT_DIR  optional, default ~/comfyui-docker
: "${ENV_B64:?ENV_B64 is required}"
: "${STAMP:?STAMP is required}"
PROJECT_DIR=${PROJECT_DIR:-$HOME/comfyui-docker}
case "$STAMP" in *[!A-Za-z0-9._-]*) echo "[error] bad STAMP: $STAMP" >&2; exit 2;; esac
cd "$PROJECT_DIR" || { echo "[error] no $PROJECT_DIR" >&2; exit 1; }
[ -f .env ] || { echo "[error] no $PROJECT_DIR/.env to back up" >&2; exit 1; }
cp -p .env ".env.bak-$STAMP"
printf '%s' "$ENV_B64" | base64 -d > .env.new
mv .env.new .env
echo "[ok] .env updated; the old one is .env.bak-$STAMP"
