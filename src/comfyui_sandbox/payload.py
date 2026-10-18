"""The docker assets (package data in `docker/`) as a tar for `deploy_comfyui.sh`.

With automount off the distro can't read the package, so the files travel as a base64 tar in
the script's `PAYLOAD` variable (docs/adr/0002).
"""

from __future__ import annotations

import base64
import io
import tarfile
from importlib import resources

ASSETS = ("Dockerfile", "docker-compose.yml", "docker-compose.gpu.yml", "entrypoint.sh")
EXECUTABLE = {"entrypoint.sh"}


def asset_text(name: str) -> str:
    """A docker asset from the package, CR stripped."""
    return (resources.files("comfyui_sandbox") / "docker" / name).read_text("utf-8").replace("\r", "")


def build(env_text: str) -> str:
    """base64 of a tar holding the docker assets and `.env` (LF endings, owner root: the
    script extracts with --no-same-owner)."""
    files = {name: asset_text(name) for name in ASSETS}
    files[".env"] = env_text
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name in EXECUTABLE else 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    return base64.b64encode(buffer.getvalue()).decode("ascii")
