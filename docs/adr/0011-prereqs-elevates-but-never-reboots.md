# `prereqs` elevates through UAC but never reboots

Installing or updating WSL (`wsl --install --no-distribution`, `wsl --update`) needs admin rights. Rather than telling the user to open an admin shell, `prereqs` self-elevates through a UAC prompt for just that command. It reports whether a reboot is needed and never reboots the machine itself.
