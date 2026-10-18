"""Links. They're underlined and written as terminal hyperlinks (OSC 8), and a click on one opens
it: Textual takes the mouse, so the terminal's own Ctrl+click may not reach them. Only things that
exist get one: a ComfyUI URL while it's up, a log, a backup. The link under the mouse is lit, as a
browser lights one."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from urllib.request import url2pathname

from rich.markup import escape
from rich.segment import Segment
from textual import events, getters
from textual.strip import Strip
from textual.widget import Widget

from .appmixin import AppMixin
from .theme import st

LINK_HOVER = st(bg="#16395a", underline=True)


def link(label: str, target: str) -> str:
    """Markup for label, linked to target."""
    return f"[underline link={target}]{escape(label)}[/]"


def url_link(port: int) -> str:
    """Markup for a sandbox's URL, linked to itself."""
    return link(f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port}")


def path_link(path: str) -> str:
    """A ~\\ path, shown as it is and linked to the file it names."""
    return link(path, (Path.home() / path.removeprefix("~\\")).as_uri())


def distro_uri(distro: str, linux_path: str) -> str:
    """The file: URI a click opens in the file manager for a folder inside the distro. Windows
    reaches it over \\\\wsl.localhost; a Linux host has no distro, so the path would be its own.
    This is the one place to change for Linux."""
    if sys.platform != "win32":
        return Path(linux_path).as_uri()
    return Path(rf"\\wsl.localhost\{distro}" + linux_path.replace("/", "\\")).as_uri()


def file_uri_path(uri: str) -> Path:
    """The path a file: URI names. A UNC one (file://wsl.localhost/...) keeps its host, which
    the URI's path alone would lose."""
    parts = urlparse(uri)
    return Path(url2pathname(f"//{parts.netloc}{parts.path}" if parts.netloc else parts.path))


def wsl_link(distro: str, linux_path: str) -> str:
    """A folder inside the distro, shown and linked as Windows reaches it (\\\\wsl.localhost\\...)."""
    unc = rf"\\wsl.localhost\{distro}" + linux_path.replace("/", "\\")
    return link(unc, distro_uri(distro, linux_path))


FOLDER = "\N{FILE FOLDER}"


def host_folder_link(path: str) -> str:
    """Markup for a folder of the host itself (a Windows drive or share), shown as it is."""
    return link(f"{FOLDER} {path}", Path(path).as_uri())


def folder_link(distro: str, linux_path: str, label: str) -> str:
    """Markup for a folder in the distro: a folder glyph and label, a click opening it."""
    return link(f"{FOLDER} {label}", distro_uri(distro, linux_path))


class TracksHover(AppMixin):
    """Mixed into an App: hovered is the target of the link under the mouse, and the HoverLinks
    widgets are redrawn when it changes. The app sees every mouse move first, whichever widget it
    lands on."""

    hovered: str | None = None

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.MouseMove):
            self.hover(self.screen.get_style_at(event.screen_x, event.screen_y).link)
        await super().on_event(event)

    def hover(self, target: str | None) -> None:
        """target: the link now under the mouse, or None. Redraws only when it changed."""
        if target != self.hovered:
            self.hovered = target
            for widget in self.screen.walk_children(HoverLinks):
                widget.refresh()


class HoverLinks(Widget):
    """Draws the link under the mouse (the app's hovered) highlighted."""

    if TYPE_CHECKING:
        app = getters.app(TracksHover)  # at runtime, Textual's own `app` finds the same App

    def render_line(self, y: int) -> Strip:
        # The highlight goes on the finished line, so render() itself knows nothing of the mouse.
        strip = super().render_line(y)
        target = self.app.hovered
        if target is None or not any(s and s.link == target for _, s, _ in strip):
            return strip
        return Strip(
            [Segment(t, s + LINK_HOVER) if s and s.link == target else Segment(t, s, c) for t, s, c in strip],
            strip.cell_length,
        )
