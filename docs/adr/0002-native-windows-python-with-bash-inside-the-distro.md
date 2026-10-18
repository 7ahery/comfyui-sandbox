# Native Windows Python, with bash only inside the distro

The CLI runs as native Windows Python and calls `wsl.exe` directly with list arguments, never through a shell. Windows-side logic is Python rather than bash run through Git Bash, which would bring path rewriting, UTF-16 output from `wsl.exe` and a dependency on Git for Windows. Work inside the distro is bash (`src/comfyui_sandbox/distro/*.sh`), shipped as package data and streamed into the distro over stdin, because with automount off the distro can't read files from the Windows side.

## Consequences

- `wsl.py` is the only module that builds `wsl.exe` argv. Every call sets `WSL_UTF8=1`, and every call into the distro passes `--cd /`.
- Scripts talk back only through env vars in, and exit code and output lines out.
- `shellcheck` runs over each script as it is streamed (with its prelude), as part of the test suite.
