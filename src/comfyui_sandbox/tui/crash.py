"""What crashed a Textual app, once it has stopped running: both screens raise it for the CLI's
crash path (a transcript with the traceback, exit 1), rather than leave Textual to print the
traceback and end with exit code 1 and no transcript."""

from __future__ import annotations

from textual.app import App
from textual.worker import WorkerFailed


def crash_of(app: App) -> BaseException | None:
    """The first unhandled exception app stopped on, or None when it ended normally. An error in a
    worker (home's flows, the run window's follow) comes as what the worker raised, not Textual's
    WorkerFailed around it, so the CLI's message names what broke."""
    # Textual 8.2.8 keeps that exception only in the private App._exception (its test pilot
    # re-raises it from there the same way) and offers no public hook to learn it: App.run()
    # swallows it, and return_code only says 1. Read in this one place, so a Textual upgrade that
    # renames it, or adds a public way, is a change here alone.
    crash = app._exception
    return crash.error if isinstance(crash, WorkerFailed) else crash
