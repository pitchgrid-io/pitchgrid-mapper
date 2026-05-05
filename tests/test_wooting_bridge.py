"""Smoke tests for the Wooting bridge wiring.

These do not exercise the native module against real hardware — they verify
the Python wrapper plumbing using a fake `pg_wooting_bridge`.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_native(monkeypatch):
    """Install a stub `pg_wooting_bridge` module for the duration of one test."""
    mod = types.ModuleType("pg_wooting_bridge")

    class FakeBridge:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.started = False
            self._queued = []

        def start(self):
            self.started = True

        def stop(self):
            self.started = False

        def set_profile(self, name):
            self.profile = name

        def set_pad_colors(self, list_):
            self.pad_colors = list(list_)

        def set_pad_overlay(self, x, y, r, g, b):
            self.overlay = (x, y, r, g, b)

        def clear_pad_overlay(self, x, y):
            self.overlay = None

        def drain_midi(self):
            out, self._queued = self._queued, []
            return out

        def status(self):
            return {"running": self.started, "connected": True, "devices": []}

    def available_profiles():
        return [
            {"name": "mpe", "label": "MPE", "description": "x"},
            {"name": "piano_sim", "label": "Piano sim", "description": "y"},
        ]

    mod.Bridge = FakeBridge
    mod.available_profiles = available_profiles
    monkeypatch.setitem(sys.modules, "pg_wooting_bridge", mod)
    yield mod


@pytest.fixture
def fake_dylibs(monkeypatch, tmp_path):
    """Make the bridge see a present analog/RGB dylib path so init proceeds."""
    from pg_isomap.config import settings

    analog = tmp_path / "libwooting_analog_sdk.dylib"
    rgb = tmp_path / "libwooting-rgb-sdk.dylib"
    analog.write_bytes(b"")
    rgb.write_bytes(b"")
    monkeypatch.setattr(settings, "wooting_analog_dylib_path", analog)
    monkeypatch.setattr(settings, "wooting_rgb_dylib_path", rgb)
    yield


def _wooting_yaml_config(tmp_path: Path) -> "ControllerConfig":  # noqa: F821
    from pg_isomap.controller_config import ControllerConfig

    yaml_text = """
DeviceName: "Wooting Test"
ControllerMIDIOutput: none
ControllerMIDIInput: none
isMPE: true
hasGlobalPitchBend: false
wootingDeviceLabel: "Wooting Test"
wootingVendorId: 0x31E3
wootingProductId: 0x1342
NumRows: 1
FirstRowIdx: 0
RowLengths: [3]
RowOffsets: []
HorizonToRowAngle: 0.0
RowToColAngle: 60.0
xSpacing: 19.0
ySpacing: 22
wootingKeycodeMap:
  0x1d: [0, 0]
  0x1b: [1, 0]
  0x06: [2, 0]
noteAssign: "30 + cumulativeIndex(x, y)"
defaultIsoRootCoordinate: [0, 0]
"""
    p = tmp_path / "Wooting Test.yaml"
    p.write_text(yaml_text)
    return ControllerConfig(p)


def test_controller_config_loads_wooting_metadata(tmp_path):
    cc = _wooting_yaml_config(tmp_path)
    assert cc.is_wooting()
    assert cc.wooting_product_id == 0x1342
    assert cc.wooting_keycode_map == {0x1d: (0, 0), 0x1b: (1, 0), 0x06: (2, 0)}


def test_keymap_for_bridge_includes_controller_notes(tmp_path):
    cc = _wooting_yaml_config(tmp_path)
    km = cc.build_wooting_keymap_for_bridge()
    assert km[0x1d] == (0, 0, 30)
    assert km[0x1b] == (1, 0, 31)
    assert km[0x06] == (2, 0, 32)


def test_bridge_drain_pumps_messages_into_midi_handler(fake_native, fake_dylibs, tmp_path):
    from pg_isomap.midi_handler import MIDIHandler
    from pg_isomap.wooting.bridge import WootingBridge

    cc = _wooting_yaml_config(tmp_path)
    midi = MIDIHandler("test")
    midi.inject_message = MagicMock(side_effect=midi.inject_message)
    wb = WootingBridge(midi, cc)
    # Simulate the Rust queue holding two messages.
    wb._native_bridge._queued = [b"\x90\x3C\x40", b"\x80\x3C\x00"]
    # One drain tick:
    out = wb._native_bridge.drain_midi()
    for m in out:
        midi.inject_message(list(m))
    assert midi.inject_message.call_count == 2
    # First call should have been NoteOn 60 vel 64.
    args0 = midi.inject_message.call_args_list[0][0][0]
    assert args0 == [0x90, 0x3C, 0x40]


def test_inject_message_round_trips_through_queue():
    from pg_isomap.midi_handler import MIDIHandler

    midi = MIDIHandler("test")
    midi.inject_message([0x90, 60, 100])
    msg, ts = midi._message_queue.get(timeout=0.5)
    assert msg == [0x90, 60, 100]
