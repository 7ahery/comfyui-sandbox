# Runs as ROOT inside the distro. Idempotent.
# Docker Engine from Docker's official apt repo, following
# https://docs.docker.com/engine/install/ubuntu/
# Required env: LINUX_USER
: "${LINUX_USER:?LINUX_USER is required}"
export DEBIAN_FRONTEND=noninteractive
# Never stop at a dpkg config-file prompt: keep the existing file, or take the
# package default when there is none.
apt_y(){ apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"; }

# Finish any dpkg run that was cut off earlier (e.g. a killed previous attempt).
dpkg --force-confdef --force-confold --configure -a
apt-get update
apt_y full-upgrade

# Remove unofficial/conflicting packages (docs: "Uninstall old versions")
conflicts=$(dpkg --get-selections docker.io docker-compose docker-compose-v2 docker-doc \
  docker-buildx podman-docker containerd runc 2>/dev/null | cut -f1 || true)
if [ -n "$conflicts" ]; then apt_y remove $conflicts; fi

apt_y install ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

cat > /etc/apt/sources.list.d/docker.sources <<SRC
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
SRC

apt-get update
apt_y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Daemon hardening defaults (merged, never clobbered).
mkdir -p /etc/docker
python3 - <<'PY'
import json, os
p = "/etc/docker/daemon.json"
cfg = {}
if os.path.exists(p):
    with open(p) as f:
        txt = f.read().strip()
        cfg = json.loads(txt) if txt else {}
cfg.setdefault("no-new-privileges", True)          # every container
cfg.setdefault("log-driver", "local")              # rotated, compressed logs
cfg.setdefault("log-opts", {"max-size": "20m", "max-file": "3"})
with open(p, "w") as f:
    json.dump(cfg, f, indent=2)
print("[ok] /etc/docker/daemon.json:", json.dumps(cfg))
PY

systemctl enable --now docker
systemctl restart docker

# Lets the user run docker without root. Note: docker group == root-equivalent
# inside this distro. Acceptable here because the distro is single-purpose and
# root is a passwordless `wsl -u root` away from Windows anyway.
usermod -aG docker "$LINUX_USER"

docker run --rm hello-world >/dev/null && echo "[ok] Docker Engine works: $(docker --version)"
docker compose version
