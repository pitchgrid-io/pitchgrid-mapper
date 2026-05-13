"""Lightweight USB-presence check for Wooting devices.

Returns the set of (vendor_id, product_id) pairs currently visible on USB
that match Wooting's vendor ID. Cheap enough to call every few seconds —
no SDK initialization, no plugin loading, no exclusive HID open. Used by
the controller-discovery loop to mark Wooting YAMLs as "available" in
the UI dropdown without having to activate the bridge.

The Wooting RGB SDK matches PIDs with a `+0/+1/+2` alt suffix (firmware
revisions on the same physical model report different PIDs in the low
nibble). We expose `pid_matches_base()` so callers can compare a YAML's
declared `wootingProductId` against detected PIDs with the same masking.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from typing import Set, Tuple

logger = logging.getLogger(__name__)

WOOTING_VID: int = 0x31E3
PID_ALT_MASK: int = 0xFFF0


def pid_matches_base(detected_pid: int, yaml_pid: int) -> bool:
    """True if a detected PID is in the same family as the YAML's declared PID."""
    return (detected_pid & PID_ALT_MASK) == (yaml_pid & PID_ALT_MASK)


def scan() -> Set[Tuple[int, int]]:
    """Return the set of (vid, pid) pairs for currently visible Wooting devices."""
    if sys.platform == "darwin":
        return _scan_macos()
    if sys.platform.startswith("linux"):
        return _scan_linux()
    if sys.platform.startswith("win"):
        return _scan_windows()
    return set()


def _scan_macos() -> Set[Tuple[int, int]]:
    try:
        proc = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-l", "-w", "0"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("Wooting USB scan failed: %s", exc)
        return set()

    out: Set[Tuple[int, int]] = set()
    # ioreg's tree dump puts each device on a `+-o ...` line followed by a
    # `{...}` block of properties. Split on the device boundary and inspect
    # each block for matching idVendor / idProduct. Lines have leading
    # tree-art (` |   +-o ...`) so the boundary pattern allows pipes/spaces.
    blocks = re.split(r"(?m)^(?=[\s|]*\+-o )", proc.stdout)
    for block in blocks:
        vid_m = re.search(r'"idVendor"\s*=\s*(\d+)', block)
        pid_m = re.search(r'"idProduct"\s*=\s*(\d+)', block)
        if not (vid_m and pid_m):
            continue
        vid = int(vid_m.group(1))
        pid = int(pid_m.group(1))
        if vid == WOOTING_VID:
            out.add((vid, pid))
    return out


def _scan_linux() -> Set[Tuple[int, int]]:
    try:
        proc = subprocess.run(
            ["lsusb"],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("Wooting USB scan failed: %s", exc)
        return set()

    out: Set[Tuple[int, int]] = set()
    # `lsusb` lines: "Bus 003 Device 005: ID 31e3:1342 Wooting 60HE v2"
    pattern = re.compile(r"ID\s+([0-9a-fA-F]{4}):([0-9a-fA-F]{4})")
    for line in proc.stdout.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        vid = int(m.group(1), 16)
        pid = int(m.group(2), 16)
        if vid == WOOTING_VID:
            out.add((vid, pid))
    return out


def _scan_windows() -> Set[Tuple[int, int]]:
    # PowerShell + WMI gives the cleanest no-extra-deps Windows listing.
    cmd = (
        "Get-WmiObject Win32_PnPEntity | "
        'Where-Object { $_.DeviceID -like "*VID_31E3*" } | '
        "Select-Object -ExpandProperty DeviceID"
    )
    # CREATE_NO_WINDOW (0x08000000) suppresses the console window the OS
    # would otherwise pop for each powershell invocation. Without it the
    # discovery loop opens a PowerShell window every 3s in the frozen
    # (windowed) build — visible only when the parent has no console.
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=5.0,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("Wooting USB scan failed: %s", exc)
        return set()

    out: Set[Tuple[int, int]] = set()
    pattern = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")
    for line in proc.stdout.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        vid = int(m.group(1), 16)
        pid = int(m.group(2), 16)
        if vid == WOOTING_VID:
            out.add((vid, pid))
    return out
