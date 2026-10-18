# The CLI holds the distro open itself

A WSL distro with no session idles out and stops, which would kill long steps partway. So the CLI starts a child `wsl.exe ... sleep infinity` while a command needs the distro up, and binds it to a kill-on-close Windows Job Object (`winapi.kill_with_parent`), so it ends with the CLI, even on a crash. The keepalive only keeps the distro up; it never starts or stops the container.
