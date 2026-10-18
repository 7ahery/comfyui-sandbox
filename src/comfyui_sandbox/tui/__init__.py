"""The comfyui-sandbox TUI, on Textual: home (the screen the CLI opens on with no command) and the
run window (what `run` shows). The CLI comes in through one door, entry.py, which its front-end
launcher imports lazily when `main()` picks the TUI; `python -m comfyui_sandbox.tui.demo` opens
the screens on the demo gateway (see demo.py). Imports inside it are relative. The rules the screens
keep, and the command line each action runs, are in docs/tui.md.

The screens see the world only through a `Gateway` (gateway/): its queries and the command lines
it runs. They import the protocol and its records and nothing behind it, so a gateway can be
swapped (the demo's fakes for the real CLI) without touching them, and the screens redesigned
without touching the CLI.

Textual is imported only from here down, and only when a screen opens: a CLI command must start
without paying for it.

    entry         what the CLI opens: home, on the real gateway
    demo          the module entry point: home or the run window, on the demo gateway
    gateway/      the Gateway protocol and its records; real/, the real gateway; demo/, the demo
                  gateway (FAKE)

Shared by both screens:

    states        the states the TUI tracks, as enums
    theme         the palette, styles and motion curves
    parts         spinners, bars, keycaps, headings, cards; fitting text to a width
    canvas        a grid of cells to paint on
    noise, logo   latent noise, and the logo drawn out of it
    logs          a log's lines (LogSession) and its wrapped, scrollable rows (WrappedLog)
    links         links and the mouse hovering over them
    press_twice   keys that act on their second press
    measure       how many rows a Rich renderable takes
    crash         what crashed an app, for the CLI's crash path

home/:

    app           SandboxApp: the screen, its layout and keys, its records of the sandboxes; run_home()
    console       Console: what the flows print, ask and wait with
    flows         home, the sandbox menu and its actions, install, Check this PC
    operations    showing a running command: its steps under a live view, or a short one under a spinner
    model         an operation's screen state, update's targets, Interrupt and Cancel
    picker, table, prompts, log_views, live_view, chrome, splash, widgets: what's drawn

run_window/:

    app           LaunchApp: the window and its keys; run_window()
    launch        Launch: `run`'s stages and output, and what the screen reads from them
    view          the banner, the loader, the full log, the bottom bar
"""
