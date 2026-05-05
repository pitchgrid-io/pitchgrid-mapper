"""Piano-action simulation driven by recorded Wooting key-travel CSVs.

Simplified 1-DOF model: the key trajectory (Wooting depth → key angle) is a
prescribed boundary condition, and only the hammer is integrated. The
linkage between key and hammer is modeled as a stiff spring while the jack
is in contact, releasing at the let-off angle. After release the hammer
flies free; at the string angle it rebounds with a coefficient of
restitution; the back-check captures it if the key is still pressed; the
repetition mechanism re-engages it when the key partially releases.

This is *not* a full Hirschkorn 5-DOF model (no whippen/jack/rep DoFs),
but the 1-DOF reduction is enough to resolve the question this script
exists to answer: "given a recorded key trajectory, what hammer head
speed do we get at string impact?"

Reference: Hirschkorn, "Dynamic Model of a Piano Action Mechanism"
(UWaterloo M.A.Sc. thesis, 2004). Chapter 2 + Appendix A.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.integrate import solve_ivp

# ---------------------------------------------------------------------------
# Geometry & inertia (Hirschkorn Appendix A.1, A.2 + regulation specs §2.3)

I_HAMMER = 2.90e-5            # kg·m², hammer moment of inertia about pivot
M_HAMMER = 0.01174            # kg
HAMMER_CM_R = float(np.hypot(0.1022, 0.0145))  # m, distance pivot → CoM
HAMMER_HEAD_R = float(np.hypot(0.1321, 0.0356))  # m, distance pivot → head
ROT_FRICTION_HAMMER = 0.00101  # N·m, A coefficient from Table A.9
G = 9.81                       # m/s²

# Regulation-derived angles (Hirschkorn §2.3):
# - 10mm key dip at front; key front lever ~0.218m → θ_k_max ≈ 0.046 rad.
# - Hammer rest height 48mm, string at 0; hammer arm ~0.137m → θ_h goes
#   from 0 (rest) to ~0.40 rad (string).
# - Let-off at hammer height 1mm → θ_h ≈ 0.387 rad → θ_k ≈ 0.044 rad (95%).
# - Repetition lever holds hammer at 3mm → θ_h ≈ 0.36 rad.
# - Back-check catches hammer at 15mm → θ_h ≈ 0.27 rad.

KEY_FRONT_LEVER = 0.218         # m
KEY_FRONT_TRAVEL = 0.010        # m
THETA_K_MAX = KEY_FRONT_TRAVEL / KEY_FRONT_LEVER

THETA_H_REST = 0.0
THETA_H_STRING = 0.40
THETA_H_LETOFF_HEIGHT_M = 0.001
THETA_H_BACKCHECK_HEIGHT_M = 0.015
THETA_H_REPETITION_HEIGHT_M = 0.003

# Convert hammer-height-from-string (m) to hammer angle. height=0 means
# at string (θ_h = THETA_H_STRING); height=48mm means at rest.
HAMMER_HEIGHT_FULL = 0.048
def height_to_theta_h(h_m: float) -> float:
    return THETA_H_STRING * (1.0 - h_m / HAMMER_HEIGHT_FULL)

THETA_H_LETOFF = height_to_theta_h(THETA_H_LETOFF_HEIGHT_M)
THETA_H_BACKCHECK = height_to_theta_h(THETA_H_BACKCHECK_HEIGHT_M)
THETA_H_REPETITION = height_to_theta_h(THETA_H_REPETITION_HEIGHT_M)

# Key angle at which the jack disengages (let-off): linearly interpolated
# from regulation. The hammer height should be 1 mm at let-off.
LET_OFF_KEY_FRACTION = 0.95
THETA_K_LETOFF = LET_OFF_KEY_FRACTION * THETA_K_MAX

# Repetition reset threshold: how far the key must come back up before the
# back-check / repetition lever cycle re-arms the action for another strike.
RESET_KEY_FRACTION = 0.50
THETA_K_RESET = RESET_KEY_FRACTION * THETA_K_MAX

# Effective transmission ratio: hammer angle per key angle while engaged.
# Picked so the hammer reaches the string just as the key reaches let-off.
TRANSMISSION_RATIO = THETA_H_STRING / THETA_K_LETOFF  # ≈ 9.4

# Linkage spring (engagement). Stiff enough that hammer follows the key
# closely during the press; under-damped so it has the typical action feel.
K_LINK = 200.0      # N·m / rad
D_LINK = 0.04       # N·m·s / rad — slightly under-damped

# String-contact penalty spring. Hertz-style power-of-3/2 omitted for
# simplicity; linear stiff spring with damping is enough to detect strikes
# and produce a clean rebound.
K_STRING = 4.0e3    # N·m / rad
D_STRING = 0.05
COEFF_RESTITUTION = 0.45  # hammer rebound off string

# Back-check spring (captures hammer after rebound while key is pressed).
K_CHECK = 50.0
D_CHECK = 0.04

# Gravity torque on hammer about its pivot (CoM offset).
T_GRAVITY = M_HAMMER * G * HAMMER_CM_R   # ~0.0118 N·m, restoring to rest


# ---------------------------------------------------------------------------
# State machine

ENGAGED, ESCAPED, AFTER_STRIKE, BACK_CHECKED = 0, 1, 2, 3


@dataclass
class Strike:
    t_us: int        # microseconds relative to recording trigger
    omega_h: float   # rad/s, hammer angular velocity at string contact
    head_speed: float  # m/s = omega_h * HAMMER_HEAD_R


@dataclass
class SimResult:
    t: np.ndarray
    theta_h: np.ndarray
    omega_h: np.ndarray
    theta_k: np.ndarray
    state: np.ndarray
    strikes: list[Strike] = field(default_factory=list)


def simulate(times_s: np.ndarray, depths: np.ndarray) -> SimResult:
    """Run the simulation for one recorded press."""
    # Interpolant for key depth (clamped to [0, 1]).
    depths_c = np.clip(depths, 0.0, 1.0)
    # Numerical derivative (used for diagnostics; the ODE just needs theta_k).
    def theta_k_of(t):
        return THETA_K_MAX * float(np.interp(t, times_s, depths_c))

    state = ENGAGED
    strikes: list[Strike] = []
    last_theta_h = THETA_H_REST

    def rhs(t, y):
        nonlocal state, last_theta_h
        theta_h, omega_h = y
        theta_k = theta_k_of(t)

        # State transitions (event-style, evaluated each RHS call — the
        # solver may step backwards a hair; that's OK, the transitions are
        # idempotent).
        if state == ENGAGED and theta_k > THETA_K_LETOFF:
            state = ESCAPED
        elif state == ESCAPED and theta_h >= THETA_H_STRING and last_theta_h < THETA_H_STRING:
            # We let the event detector below handle the strike record;
            # state will be set to AFTER_STRIKE in the strike handler.
            pass
        elif state == AFTER_STRIKE and theta_h <= THETA_H_BACKCHECK and theta_k > THETA_K_RESET:
            state = BACK_CHECKED
        elif state in (AFTER_STRIKE, BACK_CHECKED) and theta_k < THETA_K_RESET:
            state = ENGAGED

        # Compute torques.
        # Linkage: drives hammer toward (TRANSMISSION_RATIO * theta_k) when engaged.
        if state == ENGAGED:
            theta_h_target = TRANSMISSION_RATIO * theta_k
            T_link = K_LINK * (theta_h_target - theta_h) - D_LINK * omega_h
        elif state == BACK_CHECKED:
            T_link = K_CHECK * (THETA_H_BACKCHECK - theta_h) - D_CHECK * omega_h
        else:
            T_link = 0.0

        # Gravity (always present, restoring to rest).
        T_grav = -T_GRAVITY * np.cos(theta_h)  # gravity pulls hammer down → toward rest

        # Pivot friction (Coulomb, smoothed via tanh).
        T_fric = -ROT_FRICTION_HAMMER * np.tanh(omega_h / 0.05)

        # String contact penalty (only if past string angle).
        if theta_h > THETA_H_STRING:
            T_str = -K_STRING * (theta_h - THETA_H_STRING) - D_STRING * omega_h
        else:
            T_str = 0.0

        T_total = T_link + T_grav + T_fric + T_str
        last_theta_h = theta_h
        return [omega_h, T_total / I_HAMMER]

    # Strike event: hammer crossing string angle going up.
    def strike_event(t, y):
        return y[0] - THETA_H_STRING
    strike_event.terminal = False
    strike_event.direction = +1

    y0 = np.array([THETA_H_REST, 0.0])
    sol = solve_ivp(
        rhs,
        (float(times_s[0]), float(times_s[-1])),
        y0,
        method='LSODA',
        t_eval=times_s,
        events=strike_event,
        max_step=5e-4,
        atol=1e-7,
        rtol=1e-5,
    )

    # Build strike records from event data.
    for t_e, y_e in zip(sol.t_events[0], sol.y_events[0]):
        omega = float(y_e[1])
        if omega <= 0:
            continue  # crossing the wrong direction; skip
        strikes.append(
            Strike(
                t_us=int(round(t_e * 1e6 - times_s[0] * 1e6)),
                omega_h=omega,
                head_speed=omega * HAMMER_HEAD_R,
            )
        )

    # Re-derive state per t_eval point for plotting (cheap second pass).
    state_arr = np.zeros_like(sol.t, dtype=np.int8)
    s = ENGAGED
    th_prev = THETA_H_REST
    for i, ti in enumerate(sol.t):
        th = float(sol.y[0, i])
        tk = theta_k_of(ti)
        if s == ENGAGED and tk > THETA_K_LETOFF:
            s = ESCAPED
        elif s == ESCAPED and th >= THETA_H_STRING and th_prev < THETA_H_STRING:
            s = AFTER_STRIKE
        elif s == AFTER_STRIKE and th <= THETA_H_BACKCHECK and tk > THETA_K_RESET:
            s = BACK_CHECKED
        elif s in (AFTER_STRIKE, BACK_CHECKED) and tk < THETA_K_RESET:
            s = ENGAGED
        state_arr[i] = s
        th_prev = th

    theta_k_arr = np.array([theta_k_of(ti) for ti in sol.t])
    return SimResult(
        t=sol.t,
        theta_h=sol.y[0],
        omega_h=sol.y[1],
        theta_k=theta_k_arr,
        state=state_arr,
        strikes=strikes,
    )


# ---------------------------------------------------------------------------
# Loading and per-recording analysis

_FNAME_RE = re.compile(r'^key_0x([0-9A-Fa-f]+)_(\d{8}T\d{9})\.csv$')

HID_LABELS: dict[int, str] = {
    0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E', 0x09: 'F',
    0x0A: 'G', 0x0B: 'H', 0x0C: 'I', 0x0D: 'J', 0x0E: 'K', 0x0F: 'L',
    0x10: 'M', 0x11: 'N', 0x12: 'O', 0x13: 'P', 0x14: 'Q', 0x15: 'R',
    0x16: 'S', 0x17: 'T', 0x18: 'U', 0x19: 'V', 0x1A: 'W', 0x1B: 'X',
    0x1C: 'Y', 0x1D: 'Z',
    0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4', 0x22: '5', 0x23: '6',
    0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
    0x28: 'Enter', 0x29: 'Esc', 0x2A: 'Bksp', 0x2B: 'Tab', 0x2C: 'Space',
    0x2D: '-', 0x2E: '=', 0x2F: '[', 0x30: ']', 0x31: '\\',
    0x33: ';', 0x34: "'", 0x35: '`',
    0x36: ',', 0x37: '.', 0x38: '/',
}


def load_recording(path: Path) -> tuple[int, str, np.ndarray, np.ndarray]:
    m = _FNAME_RE.match(path.name)
    hid = int(m.group(1), 16)
    label = HID_LABELS.get(hid, f'0x{hid:02X}')
    df = pd.read_csv(path)
    times_s = df['t_us_relative_to_trigger'].to_numpy() * 1e-6
    depths = df['depth'].to_numpy()
    return hid, label, times_s, depths


def velocity_to_midi(head_speed: float, max_speed: float, top_velocity: int = 123) -> int:
    """Linear scaling: head_speed=max → MIDI top_velocity, with floor 1."""
    if max_speed <= 0:
        return 1
    v = round(top_velocity * head_speed / max_speed)
    return int(max(1, min(127, v)))


def main(trace_dir: Path = Path('/tmp/keyrecord')) -> None:
    paths = sorted(trace_dir.glob('key_*.csv'))
    print(f'Loaded {len(paths)} recording(s) from {trace_dir}\n')

    rows = []
    all_strikes: list[tuple[Path, Strike]] = []
    for p in paths:
        hid, label, times_s, depths = load_recording(p)
        result = simulate(times_s, depths)
        for s in result.strikes:
            all_strikes.append((p, s))
        if result.strikes:
            best = max(result.strikes, key=lambda s: s.head_speed)
            rows.append({
                'file': p.name,
                'label': label,
                'hid': f'0x{hid:02X}',
                'n_strikes': len(result.strikes),
                'best_omega_rad_s': best.omega_h,
                'best_head_m_s': best.head_speed,
                'best_strike_t_ms': best.t_us / 1000.0,
            })
        else:
            rows.append({
                'file': p.name,
                'label': label,
                'hid': f'0x{hid:02X}',
                'n_strikes': 0,
                'best_omega_rad_s': float('nan'),
                'best_head_m_s': float('nan'),
                'best_strike_t_ms': float('nan'),
            })

    summary = pd.DataFrame(rows)
    print('Per-recording summary:')
    print(summary.to_string(index=False))
    print()

    if all_strikes:
        max_speed = max(s.head_speed for _, s in all_strikes)
        n_strikes = len(all_strikes)
        print(f'Total strike events across session: {n_strikes}')
        print(f'Max hammer head speed: {max_speed:.4f} m/s '
              f'(angular {max_speed / HAMMER_HEAD_R:.2f} rad/s)')
        print(f'Mapping: head_speed = {max_speed:.4f} m/s → MIDI velocity 123')
        print()

        # Per-strike MIDI velocities at the chosen scaling.
        print('All strike events (MIDI velocity scaled so max → 123):')
        strike_rows = [
            {
                'file': p.name,
                'label': HID_LABELS.get(int(_FNAME_RE.match(p.name).group(1), 16), '?'),
                't_ms': s.t_us / 1000.0,
                'head_m_s': s.head_speed,
                'midi_velocity': velocity_to_midi(s.head_speed, max_speed),
            }
            for p, s in all_strikes
        ]
        print(pd.DataFrame(strike_rows).to_string(index=False))
        print()
        print(f'Suggested Rust constant: HEAD_SPEED_FOR_MIDI_127 ≈ '
              f'{max_speed * 127 / 123:.4f} m/s '
              f'(adds the same ~3% headroom we leave at MIDI 123).')
    else:
        print('No strikes detected in any recording.')


if __name__ == '__main__':
    main()
