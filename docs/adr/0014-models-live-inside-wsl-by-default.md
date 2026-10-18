# Models live inside WSL by default

The default (and recommended) models folder is inside the distro (`~/comfyui-models`), so `--yes` needs nothing to exist on Windows. A Windows models folder is still offered, mounted at `/mnt/models`. If the chosen Windows folder doesn't exist, the CLI creates it with ComfyUI's model subfolders. If it exists, isn't empty and has none of those subfolders, the CLI stops with a "did you mean `...\models`?" hint rather than adding subfolders to what is probably the wrong folder.
