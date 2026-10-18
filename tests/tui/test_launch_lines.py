"""What the run window reads from ComfyUI's lines as a real sandbox prints them: with a level tag
(`[INFO] `) in front, and its custom nodes under /workspace/ComfyUI (seen in a run transcript)."""

from __future__ import annotations

from comfyui_sandbox.tui.run_window.launch import Launch

REAL_LINES = [
    "[INFO] Checkpoint files will always be loaded safely.",
    "[INFO] Total VRAM 24564 MB, total RAM 65288 MB",
    "[INFO] pytorch version: 2.9.0+cu130",
    "[INFO] Python version: 3.12.3 (main, Aug 14 2025, 17:47:21) [GCC 13.3.0]",
    "[INFO] ComfyUI version: 0.3.66",
    "[INFO] comfyui-frontend-package version: 1.28.7",
    "[INFO] ",
    "Import times for custom nodes:",
    "[INFO]    0.0 seconds: /workspace/ComfyUI/custom_nodes/websocket_image_save.py",
    "[INFO]    0.2 seconds (IMPORT FAILED): /workspace/ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus",
    "[INFO] ",
    "[INFO] Starting server",
]


def test_versions_and_custom_nodes_are_read_through_the_level_tag():
    launch = Launch("comfyui", 8188, "/mnt/models")
    for line in REAL_LINES:
        launch.add(line)
    assert {k: v for k, (v, _) in launch.facts.items()} == {
        "Models": "/mnt/models",
        "PyTorch": "2.9.0+cu130",
        "Python": "3.12.3",
        "ComfyUI": "0.3.66",
        "Frontend": "1.28.7",
        "Custom nodes": "1",
    }
    assert launch.failed_nodes == ["ComfyUI_IPAdapter_plus"]
    assert launch.part == 3  # Starting the web server


def test_the_log_keeps_the_level_tag():
    launch = Launch("comfyui", 8188, "/mnt/models")
    launch.add("[INFO] ComfyUI version: 0.3.66")
    assert launch.log.lines[-1].plain == "[INFO] ComfyUI version: 0.3.66"


def test_runs_versions_line_settles_what_the_log_said():
    launch = Launch("comfyui", 8188, "/mnt/models")
    launch.add("[INFO] ComfyUI version: 0.3.66")
    arrived = launch.facts["ComfyUI"][1]
    launch.add("Versions: ComfyUI 0.40.0, Frontend 1.55.17, PyTorch 2.14.1+cu130, Python 3.12.15")
    assert {k: v for k, (v, _) in launch.facts.items()} == {
        "Models": "/mnt/models",
        "ComfyUI": "0.40.0",
        "Frontend": "1.55.17",
        "PyTorch": "2.14.1+cu130",
        "Python": "3.12.15",
    }
    assert launch.facts["ComfyUI"][1] == arrived  # already shown: it changes, it doesn't arrive again
