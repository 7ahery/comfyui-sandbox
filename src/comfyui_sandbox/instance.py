"""Names of the run-window markers (docs/adr/0010).

One marker per distro says "a run window is open". `run` takes it, `start` and `status` look at it.
It used to be called the single-instance marker. The module and `instance_name` keep that older
name on purpose: the rename to "run-window marker" is for the docs and the messages only, and
existing code names stay (GLOSSARY.md; the feat-tui-integration spec, "Naming").
"""

from __future__ import annotations


def instance_name(distro: str) -> str:
    """The marker name for the run window of `distro` (distro names compare case-insensitively)."""
    return f"comfyui-sandbox-run-{distro.lower()}"


def stopping_name(distro: str) -> str:
    """The marker name that says the run window of `distro` is stopping ComfyUI: held from the
    start of its cleanup until the cleanup is done, while the run-window marker is still held."""
    return f"comfyui-sandbox-stopping-{distro.lower()}"
