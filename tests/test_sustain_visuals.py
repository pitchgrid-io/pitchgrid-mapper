"""Sustain-pedal hold of UI / device pad visuals.

Tests the app-level deferral that keeps pads lit while CC64 is held.
Sustain state lives in MIDIHandler, but the visual deferral happens in
`app._handle_note_event` so it applies uniformly to *every* source of
note events — MIDI input, the Wooting bridge, OSC plugin echoes, and
UI clicks.
"""

from __future__ import annotations

from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

from pg_isomap.midi_handler import MIDIHandler


class _AppStub:
    """Minimal app surface for exercising the sustain-defer logic. Mirrors
    `PGIsomapApp._handle_note_event` / `_handle_sustain_change` without
    pulling in the full app constructor."""

    def __init__(self, midi: MIDIHandler):
        import threading
        self.midi_handler = midi
        self.events: List[Tuple[int, int, bool]] = []
        self._sustained_pads: set[tuple[int, int]] = set()
        self._sustained_pads_lock = threading.Lock()
        midi.on_sustain_change = self._handle_sustain_change

    def fire(self, x: int, y: int, on: bool):
        coord = (x, y)
        if on:
            with self._sustained_pads_lock:
                self._sustained_pads.discard(coord)
        else:
            if self.midi_handler.is_sustain_held():
                with self._sustained_pads_lock:
                    self._sustained_pads.add(coord)
                return
        self.events.append((x, y, on))

    def _handle_sustain_change(self, held: bool):
        if held:
            return
        with self._sustained_pads_lock:
            to_release = list(self._sustained_pads)
            self._sustained_pads.clear()
        for lx, ly in to_release:
            self.events.append((lx, ly, False))


@pytest.fixture
def app():
    midi = MIDIHandler("test")
    midi.midi_out = MagicMock()
    midi.update_note_mapping({(1, 1): 60}, {(0, 60): (1, 1)})
    return _AppStub(midi)


def _drain_midi(h: MIDIHandler):
    """Synchronously process whatever is in the queue."""
    while True:
        try:
            message, _ts = h._message_queue.get_nowait()
        except Exception:
            return
        status = message[0] & 0xF0
        if status == 0xB0 and len(message) >= 3 and message[1] == 0x40:
            h._handle_sustain_cc(message[2])
            continue
        if len(message) >= 3 and status in (0x80, 0x90):
            channel = message[0] & 0x0F
            lookup = channel if h.use_channel_for_lookup else 0
            rk = (lookup, message[1])
            if rk in h.reverse_mapping:
                coord = h.reverse_mapping[rk]
                if coord in h.note_mapping:
                    is_on = status == 0x90 and message[2] > 0
                    if h.on_note_event:
                        h.on_note_event(coord[0], coord[1], is_on)


def test_noteoff_under_sustain_is_deferred(app: _AppStub):
    midi = app.midi_handler
    midi.on_note_event = lambda x, y, on: app.fire(x, y, on)
    midi.inject_message([0xB0, 0x40, 127])
    midi.inject_message([0x90, 60, 100])
    midi.inject_message([0x80, 60, 0])
    _drain_midi(midi)
    assert app.events == [(1, 1, True)]
    assert midi.is_sustain_held()


def test_sustain_release_fires_deferred_offs(app: _AppStub):
    midi = app.midi_handler
    midi.on_note_event = lambda x, y, on: app.fire(x, y, on)
    midi.inject_message([0xB0, 0x40, 127])
    midi.inject_message([0x90, 60, 100])
    midi.inject_message([0x80, 60, 0])
    midi.inject_message([0xB0, 0x40, 0])
    _drain_midi(midi)
    assert app.events == [(1, 1, True), (1, 1, False)]


def test_renote_under_sustain_cancels_deferral(app: _AppStub):
    midi = app.midi_handler
    midi.on_note_event = lambda x, y, on: app.fire(x, y, on)
    midi.inject_message([0xB0, 0x40, 127])
    midi.inject_message([0x90, 60, 100])
    midi.inject_message([0x80, 60, 0])
    midi.inject_message([0x90, 60, 100])
    midi.inject_message([0xB0, 0x40, 0])
    _drain_midi(midi)
    assert app.events == [(1, 1, True), (1, 1, True)]


def test_osc_path_also_deferred_under_sustain(app: _AppStub):
    """The whole point of moving deferral into the app: a NoteOff arriving
    *directly* through app.fire (mimicking an OSC plugin echo, which
    bypasses MIDIHandler) must also be deferred."""
    midi = app.midi_handler
    midi.inject_message([0xB0, 0x40, 127])
    _drain_midi(midi)
    app.fire(1, 1, True)        # would-be note_on from OSC
    app.fire(1, 1, False)       # OSC echoes note_off — must be deferred
    assert app.events == [(1, 1, True)]
    midi.inject_message([0xB0, 0x40, 0])
    _drain_midi(midi)
    assert app.events == [(1, 1, True), (1, 1, False)]


def test_noteoff_without_sustain_fires_immediately(app: _AppStub):
    midi = app.midi_handler
    midi.on_note_event = lambda x, y, on: app.fire(x, y, on)
    midi.inject_message([0x90, 60, 100])
    midi.inject_message([0x80, 60, 0])
    _drain_midi(midi)
    assert app.events == [(1, 1, True), (1, 1, False)]
