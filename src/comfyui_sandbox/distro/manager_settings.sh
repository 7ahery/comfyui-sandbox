# Runs as the LINUX USER, after the first ComfyUI start finished.
# Sets the ComfyUI-Manager security level.
#   normal: only nodes from the Manager's registry install (default).
#   weak:   also allows installing custom nodes from any git URL.
# Env: SECURITY_LEVEL  weak | normal-  | normal | strict   (default: normal)
#      PROJECT_DIR     optional
# Restart the container afterwards: docker compose restart
SECURITY_LEVEL=${SECURITY_LEVEL:-normal}
case "$SECURITY_LEVEL" in weak|normal-|normal|strict) ;; *) echo "[error] bad SECURITY_LEVEL: $SECURITY_LEVEL" >&2; exit 2;; esac
U=${PROJECT_DIR:-$HOME/comfyui-docker}/data/ComfyUI/user
[ -d "$U" ] || { echo "[error] $U not there yet (first install still running?)" >&2; exit 1; }
# Manager 4.x keeps its config in __manager; older versions in default/ComfyUI-Manager.
cfg=""
for c in "$U/__manager/config.ini" "$U/default/ComfyUI-Manager/config.ini"; do [ -f "$c" ] && cfg=$c && break; done
[ -n "$cfg" ] || { cfg="$U/__manager/config.ini"; mkdir -p "$(dirname "$cfg")"; }
CFG="$cfg" LEVEL="$SECURITY_LEVEL" python3 - <<'PY'
import configparser, os
p, lvl = os.environ["CFG"], os.environ["LEVEL"]
c = configparser.ConfigParser(strict=False)
c.optionxform = str
c.read(p)
if "default" not in c:
    c["default"] = {}
c["default"]["security_level"] = lvl
with open(p, "w") as f:
    c.write(f)
print(f"[ok] {p}: security_level = {lvl}")
PY
echo "[next] docker compose restart (in ~/comfyui-docker) to apply"
