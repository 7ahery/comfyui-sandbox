# Runs as ROOT inside the distro. Idempotent.
# NVIDIA Container Toolkit so containers can use the GPU.
# The GPU DRIVER comes from Windows; never install a Linux NVIDIA driver in WSL.
export DEBIAN_FRONTEND=noninteractive
# Never stop at a dpkg config-file prompt (see install_docker.sh).
apt_y(){ apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold "$@"; }

if [ ! -e /dev/dxg ]; then
  echo "[error] /dev/dxg missing: WSL GPU paravirtualization is not available." >&2
  echo "        Update the Windows NVIDIA driver and run 'wsl --update'." >&2
  exit 1
fi
if [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
  /usr/lib/wsl/lib/nvidia-smi -L
else
  echo "[warn] /usr/lib/wsl/lib/nvidia-smi not found; continuing"
fi

# Finish any dpkg run that was cut off earlier (e.g. a killed previous attempt).
dpkg --force-confdef --force-confold --configure -a
apt_y install curl gnupg
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update
apt_y install nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

# Smoke test: a container sees the GPU through the Windows driver.
if docker run --rm --gpus all ubuntu nvidia-smi -L; then
  echo "[ok] containers can see the GPU"
else
  echo "[error] a test container could not see the GPU. Update the Windows NVIDIA driver, run 'wsl --update', and retry." >&2
  exit 1
fi
