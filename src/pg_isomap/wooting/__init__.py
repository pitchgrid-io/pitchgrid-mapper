"""Wooting analog keyboard integration.

The native bridge (Rust + PyO3, package `pg_wooting_bridge`) owns the polling
thread and per-key state machines. This Python module wraps it and feeds its
output into `MIDIHandler.inject_message`, so Wooting events flow through the
exact same input pipeline as a real MIDI controller.
"""

from .bridge import WootingBridge, WootingNotAvailable

__all__ = ["WootingBridge", "WootingNotAvailable"]
