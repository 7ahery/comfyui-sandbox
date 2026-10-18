"""The base of the mixins that are mixed into an App (TracksHover, WakesOnInput)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # To the type checker a mixin is an App, so `self.screen` and `super().on_event` resolve. At
    # runtime it stays a plain class, which is what a mixin is: the App it lands in supplies both.
    from textual.app import App as AppMixin
else:
    AppMixin = object
