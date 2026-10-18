"""A memo for a render method's still frame."""

from __future__ import annotations

from collections.abc import Callable

_NOTHING = object()  # the key before the first call: equal to no real key, so that call makes


class Memo[T]:
    """The last thing made and the key it was made for. A widget whose render() paints the same
    frame until its key changes asks the memo for it: `self.still(key, lambda: self.paint(...))`.
    The memo is a cache, not widget state, and that mutation is the whole of what it does:
    render() only reads the widget and calls it, so it assigns nothing to the widget.

    It exists because render() must be a pure read (animation can't depend on how often Textual
    redraws), yet a still logo should be painted once and reused: with a plain `self.frame = ...`
    in render() the cache would be an assignment there. Used by the hero and the run window's banner."""

    _value: T  # set by the first call: its key is _NOTHING, which no real key equals

    def __init__(self) -> None:
        self._key: object = _NOTHING

    def __call__(self, key: object, make: Callable[[], T]) -> T:
        """What make() gave for this key: it runs again only when the key differs from last time's."""
        if key != self._key:
            self._value, self._key = make(), key
        return self._value
