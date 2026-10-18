"""FAKE: what a running ComfyUI prints. The demo's `logs --follow`, its Run here (`run` in home)
and its run window play it; the real gateway gets ComfyUI's own output from those commands."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .operation import DemoOperation


def comfy_startup(port: int) -> list[str]:
    """What ComfyUI prints as its container starts."""
    return [
        "Checkpoint files will always be loaded safely.",
        "Total VRAM 24564 MB, total RAM 65304 MB",
        "pytorch version: 2.9.0+cu130",
        "Set vram state to: NORMAL_VRAM",
        "Device: cuda:0 NVIDIA GeForce RTX 4090 : cudaMallocAsync",
        "Using pytorch attention",
        "Python version: 3.12.3 (main, Aug 14 2026, 17:47:21) [GCC 13.3.0]",
        "ComfyUI version: 0.3.66",
        "ComfyUI frontend version: 1.28.7",
        "[Prompt Server] web root: /app/ComfyUI/web",
        "[ComfyUI-Manager] default cache updated: "
        "https://raw.githubusercontent.com/ltdrdata/ComfyUI-Manager/main/model-list.json",
        "",
        "Import times for custom nodes:",
        "   0.0 seconds: /app/ComfyUI/custom_nodes/websocket_image_save.py",
        "   0.1 seconds: /app/ComfyUI/custom_nodes/comfyui_controlnet_aux",
        "   0.4 seconds: /app/ComfyUI/custom_nodes/ComfyUI-Manager",
        "",
        "Starting server",
        "",
        f"To see the GUI go to: http://0.0.0.0:{port}",
    ]


def comfy_job() -> list[tuple[float, str]]:
    """One prompt's worth of output, as (seconds before the line, the line)."""
    model = random.choice([("SDXLClipModel", "SDXL", 9.5, 4.9), ("FluxClipModel_", "Flux", 9.3, 11.2)])
    its = random.uniform(4.5, 7.5)
    out = [
        (0.0, "got prompt"),
        (0.2, "model weight dtype torch.float16, manual cast: None"),
        (0.1, f"Requested to load {model[0]}"),
        (0.6, f"loaded completely {model[2]} GB"),
        (0.1, f"Requested to load {model[1]}"),
        (0.8, f"loaded completely {model[3]} GB"),
    ]
    for k in (5, 10, 15, 20):
        bars = "█" * (k // 2)
        out.append(
            (
                5 / its,
                f"{k * 5:3d}%|{bars:<10}| {k}/20 [00:{int(k / its):02d}<00:{int((20 - k) / its):02d}, {its:.2f}it/s]",
            )
        )
    if random.random() < 0.12:
        out.append((0.2, "WARNING: Ran out of memory when regular VAE decoding, retrying with tiled VAE decoding."))
    out += [
        (0.1, "Requested to load AutoencoderKL"),
        (0.2, "loaded completely 0.2 GB"),
        (0.3, f"Prompt executed in {20 / its + 1.9:.2f} seconds"),
    ]
    return out


def backlog(port: int, tail: int) -> list[str]:
    """The last tail lines ComfyUI printed (logs --tail): its start-up, then prompts."""
    past = comfy_startup(port)
    while len(past) < tail * 2:
        past += [line for _, line in comfy_job()]
    return past[-tail:]


async def feed(op: DemoOperation) -> None:
    """New output, as a running ComfyUI prints it: a prompt now and then, until op is cancelled."""
    while True:
        await op.nap(random.uniform(0.5, 2.5))
        for delay, line in comfy_job():
            await op.nap(delay)
            op.say(line)
