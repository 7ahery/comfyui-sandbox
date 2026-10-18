"""What the run window draws: the banner (the logo resolving as ComfyUI loads), the loader under
it (the sandbox's name, what it runs, one status line), the full log, and the bottom bar."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from rich.style import Style as RichStyle
from rich.text import Text
from textual import events, getters
from textual.widget import Widget

from ..canvas import Canvas
from ..links import LINK_HOVER
from ..logo import LOGO_W, SUB, VERSION, LogoLook, paint_logo
from ..logs import LogSession, WrappedLog
from ..memo import Memo
from ..noise import glitch_level
from ..parts import bar_cells, center, elide, keycap, keys_line, pad, spinner
from ..states import LaunchPhase
from ..theme import (
    AMBER,
    FAINT,
    GREEN,
    INK,
    LINE,
    MINT,
    MUTED,
    RED,
    SKY,
    SOFT,
    TEXT,
    VIOLET,
    WHITE,
    breathe,
    clamp,
    ease,
    mix,
    st,
)
from .launch import Launch

if TYPE_CHECKING:
    from .app import LaunchApp


class LaunchWidget(Widget):
    """A widget of the run window, whose `app` is the LaunchApp it runs in."""

    if TYPE_CHECKING:
        app = getters.app(LaunchApp)  # at runtime, Textual's own `app` finds the same App


def live_dot() -> Text:
    """The ● by "Running at": it breathes (1.6 s), which is why LaunchApp redraws the loader at 10 Hz."""
    return Text("●", st(mix(GREEN, INK, 0.5 * breathe(1.6)), bold=True))


# -- what ComfyUI runs -------------------------------------------------------------------------

# The grid under the sandbox's name, row by row: ComfyUI's own parts, then what it runs on and
# where its models are. Each value's room (what it usually takes: a real sandbox's PyTorch is
# 2.14.1+cu130, its frontend 1.55.17) keeps the grid from shifting as the values arrive.
FACT_GRID = [[("ComfyUI", 6), ("Frontend", 7), ("Custom nodes", 14)], [("PyTorch", 12), ("Python", 7), ("Models", 14)]]
# A narrow window: two columns, three rows, so the models folder keeps a readable width.
FACT_GRID_NARROW = [
    [("ComfyUI", 6), ("PyTorch", 12)],
    [("Frontend", 7), ("Python", 7)],
    [("Custom nodes", 14), ("Models", 14)],
]
MODELS_MAX = 40  # the models folder's most room; longer, it's cut, so the block stays compact
FACT_RULE = "   │   "


def elide_path(path: str, width: int) -> str:
    """path cut to width at its separators: the start (a drive, a share, ~) and as many of its
    last folders as fit, with … for what's left out, so both ends stay whole words. Only a last
    folder too long by itself is cut, keeping the start of its name."""
    if len(path) <= width:
        return path
    sep = "\\" if "\\" in path else "/"
    lead = "\\\\" if path.startswith("\\\\") else ""
    parts = path[len(lead) :].split(sep)
    head = lead + parts[0] + sep + "…"
    tail: list[str] = []
    for part in reversed(parts[1:]):
        candidate = sep.join([part, *tail])
        if len(head) + 1 + len(candidate) > width:
            break
        tail.insert(0, part)
    if tail:
        return head + sep + sep.join(tail)
    room = width - len(head) - 2  # the last folder alone is too long: keep the start of its name
    if room >= 6:
        return head + sep + parts[-1][:room] + "…"
    return elide(path, width)


def fact_value(launch: Launch, key: str, room: int, t: float, k: int) -> Text:
    """A value as printed, glowing for a moment after it arrives; before that a faint
    placeholder, breathing while ComfyUI loads."""
    if key not in launch.facts:
        dim = mix(LINE, FAINT, breathe(1.4, k * 0.15)) if launch.phase == LaunchPhase.STARTING else LINE
        return Text("·" * min(room, 11), st(dim))
    value, since = launch.facts[key]
    fitted = elide_path(value, room) if key == "Models" else elide(value, room)
    out = Text(fitted, st(mix(WHITE, TEXT, clamp((t - since) / 0.6)), bold=True))
    if key == "Custom nodes" and launch.failed_nodes:
        out.append(f" · {len(launch.failed_nodes)} failed", st(AMBER))
    if key == "ComfyUI" and launch.update and launch.phase == LaunchPhase.RUNNING:
        out.append(" ↑", st(VIOLET, bold=True))  # the column's room is PyTorch's: it fits
    elif key == "ComfyUI" and launch.pinned and launch.phase == LaunchPhase.RUNNING:
        out.append(" ◆", st(SKY, bold=True))  # pinned: the mark in the update mark's place
    return out


def facts_grid(launch: Launch, width: int, t: float) -> list[Text]:
    """ComfyUI, the frontend, the custom nodes, PyTorch, Python and the models folder: rows of
    quiet labels and values, in columns divided by thin rules, centred as one block. The models
    folder, last, gets what's left of the width; when that's less than it needs, three rows
    instead of two."""
    for grid in (FACT_GRID, FACT_GRID_NARROW):
        columns = range(len(grid[0]))
        label_w = [max(len(row[c][0]) for row in grid) for c in columns]
        room = [max(row[c][1] for row in grid) for c in columns]
        fixed = sum(label_w) + 2 * len(label_w) + sum(room[:-1]) + len(FACT_RULE) * (len(room) - 1)
        room[-1] = max(room[-1], min(len(launch.models), width - 4 - fixed, MODELS_MAX))
        if room[-1] >= min(len(launch.models), MODELS_MAX):
            break  # two rows show it as fully as it will be shown; else three rows give it more room
    lines = []
    for r, row in enumerate(grid):
        cells = []
        for c, (key, _) in enumerate(row):
            cell = Text.assemble(
                (key.ljust(label_w[c]), st(FAINT)), "  ", fact_value(launch, key, room[c], t, r * len(row) + c)
            )
            cells.append(pad(cell, label_w[c] + 2 + room[c]))
        lines.append(Text(FACT_RULE, st(LINE)).join(cells))
    left = " " * max(0, (width - lines[0].cell_len) // 2)
    return [Text.assemble(left, line) for line in lines]


def close_notice(launch: Launch) -> Text:
    """That closing the window stops ComfyUI: always on screen while that's true. The words are
    `run`'s own stop notice, as the gateway passes it on: the core knows what ending its window
    does (its console-close handler), so it says so; the window only keeps it in sight."""
    if launch.phase == LaunchPhase.STOPPING:
        return Text.assemble(
            ("▍ ", st(SKY)),
            ("Stopping ComfyUI… ", st(TEXT, bold=True)),
            ("this window closes when it's done.", st(MUTED)),
        )
    if not launch.alive():
        return Text.assemble(("▍ ", st(FAINT)), ("ComfyUI isn't running. Closing this window is safe.", st(MUTED)))
    if not launch.notice:
        return Text("")
    return Text.assemble(("▍ ", st(AMBER)), (launch.notice, st(TEXT, bold=True)))


# -- the banner --------------------------------------------------------------------------------

BODY_ROWS = 13  # the loader's lines under the banner, the close notice included


def banner_scale(width: int, height: int) -> int:
    """How many cells a logo pixel is: the biggest scale (3, 2, else 1) that fits the window's
    width and still leaves BODY_ROWS for the loader under the banner."""
    for scale in (3, 2):
        if width >= LOGO_W * scale + 4 and height - banner_rows(scale) - 2 >= BODY_ROWS:
            return scale
    return 1


def banner_rows(scale: int) -> int:
    return 2 * scale + 2  # the logo, a blank, the wordmark


def banner_top(height: int, scale: int) -> int:
    """Rows above the logo: the logo and its few lines sit in the middle, as a loading screen's do."""
    return max(1, (height - banner_rows(scale) - 2 - BODY_ROWS) // 2)


# As home's hero (chrome.DRIFT_STEP): a resolved, settled logo only drifts in colour, so it is
# painted once per step and reused (Banner.still) in between.
STOP_SECONDS = 1.6  # about how long stopping takes: the logo dissolves over it
DRIFT_STEP = 0.5  # seconds between the repaints of a still banner: the logo's colours drift 0.03 a second
EASE_RATE = 2.6  # per second: the banner closes this share (as e^-rate) of its gap to the real progress


def ease_toward(shown: float, target: float, dt: float) -> float:
    """shown, moved toward target over dt seconds. Small steps add up to the same as one big one."""
    return shown + (target - shown) * (1 - math.exp(-EASE_RATE * dt))


class Banner(LaunchWidget):
    """The splash's banner: the logo, scaled up, with S A N D B O X and the version under it. The
    logo is the load's progress: it resolves out of latent noise as ComfyUI loads. A failure tints
    it red and glitches it; stopping dissolves it."""

    def __init__(self) -> None:
        super().__init__(id="banner")
        self.shown = 0.0  # the progress the logo shows, eased toward the real one
        self.advanced = time.monotonic()  # when advance() last moved it
        self.still: Memo[Text] = Memo()  # the still banner (below), for what it was painted for

    def advance(self, t: float) -> None:
        """Moves the shown progress toward the real one by the time since the last call (the app's
        frame calls it), so the logo resolves at one speed however often the screen redraws.

        Easing by elapsed time, not by a fixed share per call, is what lets the app's Pacer change
        its rate (20 Hz while loading, 2 Hz idle) without changing how fast the logo resolves; and
        it is done here, in the frame, because render() may not assign `shown`."""
        launch = self.app.launch
        dt, self.advanced = t - self.advanced, t
        if launch.phase == LaunchPhase.STARTING:
            self.shown = ease_toward(self.shown, launch.progress(t), dt)
        elif launch.phase == LaunchPhase.ELSEWHERE:
            self.shown = ease_toward(self.shown, 1.0, dt)  # the splash's resolve: nothing's loading here
        else:
            self.shown = max(self.shown, launch.progress(t)) if launch.phase == LaunchPhase.FAILED else 1.0

    def render(self) -> Text:
        # A pure read of the app's launch and the clock (`shown` moves in advance()), so what is drawn
        # doesn't depend on how often Textual redraws.
        launch, t = self.app.launch, time.monotonic()
        # 0.8: the ready sheen is over; 0.9: the wordmark has faded in (paint's 0.4 + 0.5 s).
        if (
            launch.phase == LaunchPhase.RUNNING
            and self.shown >= 1.0
            and launch.stop_started is None
            and launch.ready_at is not None
            and t - launch.ready_at >= 0.8
            and t - launch.born >= 0.9
        ):
            # Resolved and settled: only the colours' slow drift moves it, so paint it once per step.
            # The key is what paint() draws from besides the time: the sizes it fits to, and the
            # port (the noise's seed). The step's start is the time it's painted at.
            step = int(t / DRIFT_STEP)
            return self.still((self.size, self.app.size, launch.port, step), lambda: self.paint(step * DRIFT_STEP))
        return self.paint(t)

    def paint(self, t: float) -> Text:
        """The banner as of time t: the logo (resolved as far as `shown`; red and glitched on a
        failure; dissolving while stopping) with the wordmark under it."""
        app = self.app
        launch = app.launch
        c = Canvas(self.size.width, self.size.height)
        scale = banner_scale(app.size.width, app.size.height)
        lx, ly = (c.width - LOGO_W * scale) // 2, banner_top(app.size.height, scale)
        noise = 1 - self.shown
        tint = glitch = None
        load = launch.load_stage()
        failed_at = load.ended if load is not None else None
        if launch.phase == LaunchPhase.FAILED:
            tint, glitch = RED, glitch_level(t, failed_at or t)
        if launch.stop_started is not None:
            noise = max(noise, ease((t - launch.stop_started) / STOP_SECONDS))
        sheen = None
        if launch.ready_at is not None and 0 < t - launch.ready_at < 0.8:
            sheen = (t - launch.ready_at) / 0.8 * (LOGO_W + 40) - 20
        paint_logo(
            c,
            lx,
            ly,
            t,
            scale=scale,
            sheen=sheen,
            look=LogoLook(
                noise=noise,
                seed=launch.port,
                noise_t=failed_at if launch.phase == LaunchPhase.FAILED else None,
                tint=tint,
                glitch=glitch or 0.0,
            ),
        )

        fade = clamp((t - launch.born - 0.4) / 0.5)
        sub = f"{SUB}   {VERSION}"
        x = c.put((c.width - len(sub)) // 2, ly + 2 * scale + 1, SUB, mix(INK, SOFT, fade), bold=True)
        c.put(x + 3, ly + 2 * scale + 1, VERSION, mix(INK, FAINT, fade))
        return c.text()


# -- the loader --------------------------------------------------------------------------------


def url_text(launch: Launch, hovered: str | None) -> Text:
    """The URL as a link: a click opens it; under the mouse it's lit, as a browser lights one."""
    url = launch.url()
    style = st(SKY, bold=True, underline=True) + RichStyle(link=url)
    return Text(url, style + LINK_HOVER if hovered == url else style)


def log_line(path: str, width: int) -> str:
    """ "Log: <transcript>" in width: a path too long loses its start, not its end, since the
    transcript's own name (its time and command) is what tells it from the others."""
    line = f"Log: {path}"
    if len(line) <= width:
        return line
    return "Log: …" + path[len(path) - max(1, width - len("Log: …")) :]


def loader(launch: Launch, width: int, height: int, t: float, hovered: str | None = None) -> list[Text]:
    """Under the banner: the sandbox's name and what it runs, then one status line (and a bar
    while it loads). hovered: the link under the mouse."""
    out = [Text(""), center(Text(launch.distro, st(TEXT, bold=True)), width), *facts_grid(launch, width, t), Text("")]
    if launch.phase == LaunchPhase.ELSEWHERE:
        out += [
            center(Text.assemble(live_dot(), ("  Already running in another window", st(TEXT))), width),
            center(
                Text.assemble(
                    ("Opened ", st(FAINT)),
                    url_text(launch, hovered),
                    (" in your browser. This window closes on its own.", st(FAINT)),
                ),
                width,
            ),
        ]
    elif launch.phase == LaunchPhase.RUNNING:
        out += [
            center(Text.assemble(live_dot(), ("  Running at ", st(TEXT)), url_text(launch, hovered)), width),
            center(Text("opened in your browser", st(FAINT)), width),
        ]
        if launch.update or launch.pinned or launch.failed_nodes:
            out.append(Text(""))
        if launch.update:
            out.append(
                center(Text.assemble(("↑ ", st(VIOLET, bold=True)), (f"{launch.update} is available", st(TEXT))), width)
            )
        elif launch.pinned:  # a pinned sandbox is never told of a newer one: it says where it's held
            out.append(
                center(
                    Text.assemble(("◆ ", st(SKY, bold=True)), (f"Pinned to ComfyUI {launch.pinned}", st(TEXT))), width
                )
            )
        if launch.failed_nodes:
            out.append(center(Text(f"{launch.failed_nodes[0]} failed to import · the log says why", st(AMBER)), width))
    elif launch.phase == LaunchPhase.FAILED:
        # run's own message says what went wrong; its transcript has the rest. The window stays
        # open on it until a key closes it, as the plain window waits for one.
        what = "ComfyUI stopped" if launch.ready_at is not None else "ComfyUI stopped before it was ready"
        out += [
            center(Text.assemble(("✕ ", st(RED, bold=True)), (what, st(TEXT, bold=True))), width),
            # Its first line: a report's last log lines are in the full log (L), under "Error:".
            center(Text((launch.error or "").partition("\n")[0], st(RED)), width),
        ]
        if launch.log_path:
            out += [Text(""), center(Text(log_line(launch.log_path, width - 4), st(MUTED)), width)]
    elif launch.phase in (LaunchPhase.STOPPING, LaunchPhase.STOPPED):
        stopped = launch.phase == LaunchPhase.STOPPED
        out.append(
            center(
                Text.assemble(
                    Text("✓", st(MINT, bold=True)) if stopped else spinner(SKY, t),
                    " ",
                    ("ComfyUI has been stopped" if stopped else "Stopping ComfyUI", st(TEXT)),
                ),
                width,
            )
        )
    else:
        doing = launch.doing()
        out.append(center(Text.assemble(spinner(SKY, t), " ", (doing + "…" if doing else "", st(TEXT))), width))
        out.append(Text(""))
        line = Text()
        for ch, color in bar_cells(launch.progress(t), min(44, width - 8), sheen=False):
            line.append(ch, st(color))
        out.append(center(line, width))
    out.append(Text(""))
    out.append(center(Text.assemble(keycap("l"), (" show the full log", st(FAINT))), width))
    while len(out) < height - 2:
        out.append(Text(""))
    out.append(center(close_notice(launch), width))
    return out


class Summary(LaunchWidget):
    """The loader under the banner, redrawn whole: its dot breathes, its values glow as they
    arrive, and the main frame and the dot's own Pacer both refresh it."""

    def render(self) -> Text:
        app = self.app
        width, height = self.size.width, self.size.height
        lines = loader(app.launch, width, height, time.monotonic(), app.hovered)
        if len(lines) > height:  # a small window: the close notice (always last) keeps its row
            lines = [*lines[: height - 1], lines[-1]]
        return Text("\n", no_wrap=True, overflow="crop").join(pad(line, width) for line in lines[:height])


# -- the full log --------------------------------------------------------------------------------


class LogPane(WrappedLog, LaunchWidget):
    """ComfyUI's whole output, over everything (L), its long lines wrapped (WrappedLog, which
    home's Show logs uses too): at the bottom it keeps up with new lines; scrolled back it holds
    the reader's place, even through a resize. Home's LogView, without the page around it."""

    def session(self) -> LogSession:
        return self.app.launch.log

    def rows(self) -> int:
        return max(1, self.size.height - 2)

    def text_width(self, overflow: bool) -> int:
        return self.size.width - (2 if overflow else 0)  # the scrollbar and a space

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.scroll_lines(-3)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.scroll_lines(3)

    def render(self) -> Text:
        launch: Launch = self.app.launch
        width = self.size.width
        view, first, last = self.view()
        thumb = self.thumb(first, last)
        # The command on the left, where the reader is on the right, over the scrollbar's column.
        # The accent bar, as home's headings have it, marks the command whose output this is.
        command = Text.assemble(("▍", st(SKY)), " ", (f"comfyui-sandbox run --distro {launch.distro}", st(FAINT)))
        place = Text(self.where(first, last), st(FAINT))
        out = [
            Text.assemble(command, " " * max(1, width - command.cell_len - place.cell_len), place),
            Text(""),
            *self.scrolled_rows(view, thumb),
        ]
        return Text("\n", no_wrap=True).join(out)


# -- the bottom bar ------------------------------------------------------------------------------


class BottomBar(LaunchWidget):
    """The keys that work now, and what closing the window does while ComfyUI runs."""

    def render(self) -> Text:
        app = self.app
        launch, width = app.launch, self.size.width
        keys: list[tuple[str, str]]
        if app.prompt is not None:
            # A question's own keys. Not its Ctrl+C: here that stops ComfyUI, as the note below says.
            keys = [key for key in app.prompt.keys() if key[0] != "ctrl+c"]  # noqa: SIM118 - a prompt, not a dict
        elif app.logs:
            keys = [("↑↓ pgup pgdn", "scroll"), ("l esc", "back")]  # Home and End work, unlisted
        else:
            keys = [("l", "full log")]
        if launch.phase in (LaunchPhase.FAILED, LaunchPhase.STOPPED):
            keys.append(("enter", "close"))
        left = keys_line(keys)
        # Ctrl+C isn't offered here (in Windows Terminal it also copies a selection), but a first
        # press is answered, so a stray one doesn't stop ComfyUI unannounced.
        armed = app.armed.key()
        if armed:
            left.append(f"   ▲ press {armed} again to stop ComfyUI and close this window", st(AMBER, bold=True))
        right = Text("")
        if launch.alive() and not armed and app.logs:  # the loader says it itself
            right = Text.assemble(("◆ ", st(AMBER)), ("closing this window stops ComfyUI ", st(MUTED)))
        gap = width - left.cell_len - right.cell_len
        return Text.assemble(left, " " * gap, right) if gap >= 2 else pad(left, width)
