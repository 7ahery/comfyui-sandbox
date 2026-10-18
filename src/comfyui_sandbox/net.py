"""HTTP from Windows, injected (docs/adr/0013): the one place that talks to localhost ports."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HttpReply:
    status: int
    body: str


class Http(Protocol):
    def get(self, url: str, *, timeout: float = 10) -> HttpReply:
        """GET `url` (no proxy). Raises OSError when nothing answers."""
        ...


class UrllibHttp:
    def __init__(self) -> None:
        # Never go through a system proxy: these are loopback addresses.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, url: str, *, timeout: float = 10) -> HttpReply:
        try:
            with self._opener.open(url, timeout=timeout) as response:
                return HttpReply(response.status, response.read(1_000_000).decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:  # an answer, just not a 2xx
            return HttpReply(exc.code, exc.read(1_000_000).decode("utf-8", "replace"))
        except urllib.error.URLError as exc:
            raise OSError(str(exc.reason)) from exc
