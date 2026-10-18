# Runs as the LINUX USER, after the first ComfyUI install finished.
# Compares the models folder with the folder set of the installed ComfyUI
# version (its own, unused data/ComfyUI/models) and lists what is missing.
# Env: MODELS_DIR (host path, default /mnt/models), CREATE=1 to create missing
#      folders (empty dirs only; never touches files), PROJECT_DIR optional.
MODELS_DIR=${MODELS_DIR:-/mnt/models}
REF=${PROJECT_DIR:-$HOME/comfyui-docker}/data/ComfyUI/models
[ -d "$REF" ] && [ ! -L "$REF" ] || { echo "[info] $REF not there yet (first install still running?)"; exit 0; }
missing=()
while IFS= read -r d; do
  [ -d "$MODELS_DIR/$d" ] || missing+=("$d")
done < <(cd "$REF" && find . -mindepth 1 -type d -printf '%P\n' | sort)
if [ ${#missing[@]} -eq 0 ]; then echo "[ok] $MODELS_DIR has every folder ComfyUI expects"; exit 0; fi
echo "[info] missing in $MODELS_DIR: ${missing[*]}"
if [ "${CREATE:-0}" = 1 ]; then
  for d in "${missing[@]}"; do mkdir -p "$MODELS_DIR/$d"; done
  echo "[ok] created ${#missing[@]} folder(s); restart the container to pick them up"
fi
