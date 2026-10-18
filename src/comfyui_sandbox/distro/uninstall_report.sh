# Runs as the LINUX USER inside the distro. Read-only.
# Counts what an uninstall (wsl --unregister) would delete, so the user can
# decide on a backup before confirming. Prints counts only: no file names,
# paths or contents, which stay out of the agent's context. Changes nothing.
# One `key=value` line each; the CLI turns them into the report. Values are
# numbers or fixed words (models=windows|wsl|unknown), plus the disk size.
PROJECT_DIR=${PROJECT_DIR:-$HOME/comfyui-docker}
CU=$PROJECT_DIR/data/ComfyUI

count(){ [ -d "$1" ] && find "$1" "${@:2}" 2>/dev/null | wc -l || echo 0; }

echo "workflows=$(count "$CU/user" -path '*/workflows/*' -type f -name '*.json')"
echo "input_files=$(count "$CU/input" -type f ! -name '.*')"
echo "output_files=$(count "$CU/output" -type f ! -name '_output_images_will_be_put_here')"
echo "custom_nodes=$(count "$CU/custom_nodes" -mindepth 1 -maxdepth 1 -type d ! -name '__pycache__')"
echo "backups=$(count "$HOME/comfyui-backups" -maxdepth 1 -type f -name '*.tar')"

models=$(sed -n 's/^MODELS_DIR=//p' "$PROJECT_DIR/.env" 2>/dev/null | tr -d '"')
if [ "$models" = /mnt/models ] || mountpoint -q /mnt/models 2>/dev/null; then
  echo "models=windows"
elif [ -n "$models" ]; then
  echo "models=wsl"
  echo "model_files=$(count "$models" -type f)"
else
  echo "models=unknown"
fi
echo "disk_used=$(df -h / | awk 'NR==2{print $3}')"
