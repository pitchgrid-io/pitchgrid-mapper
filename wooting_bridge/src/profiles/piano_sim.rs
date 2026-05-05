//! Piano-action physics simulation profile.
//!
//! Per-key 1-DOF hammer ODE driven by the recorded analog depth signal as a
//! prescribed boundary condition. While the jack is engaged, the hammer
//! tracks a stiff target proportional to key angle. At let-off the linkage
//! breaks and the hammer flies free; on string contact we record the strike
//! velocity and rebound. The repetition mechanism lets a partial-release +
//! repress trigger another strike without a full key release.
//!
//! Reference: Hirschkorn, "Dynamic Model of a Piano Action Mechanism"
//! (UWaterloo M.A.Sc. thesis, 2004), §1.2 (geometry), §2 (dynamics +
//! contacts), §2.3 (regulation), Appendix A (parameters).

use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::time::Instant;

use smallvec::SmallVec;

use crate::channels::ChannelAllocator;
use crate::midi::{
    channel_pressure, control_change, mpe_configuration_zone, note_off, note_on, MidiBatch,
};

use super::{InputProfile, KeyDescriptor, ProfileConfig, ProfileMeta};

// --- Action geometry / inertia (Hirschkorn Appendix A.1) ----------------

const I_HAMMER: f32 = 2.90e-5; // kg·m²
const M_HAMMER: f32 = 0.01174; // kg
const HAMMER_CM_R: f32 = 0.10322508; // sqrt(0.1022² + 0.0145²)
const HAMMER_HEAD_R: f32 = 0.13687161; // sqrt(0.1321² + 0.0356²)
const ROT_FRICTION_HAMMER: f32 = 0.00101; // N·m, Table A.9
const G: f32 = 9.81;

// Regulation-derived angles (Hirschkorn §2.3).
const KEY_FRONT_LEVER: f32 = 0.218; // m
const KEY_FRONT_TRAVEL: f32 = 0.010; // m
const THETA_K_MAX: f32 = KEY_FRONT_TRAVEL / KEY_FRONT_LEVER;
const THETA_H_REST: f32 = 0.0;
const THETA_H_STRING: f32 = 0.40;
const HAMMER_HEIGHT_FULL: f32 = 0.048;

// height_to_theta_h(0.001) ≈ 0.392, etc. Pre-computed.
const THETA_H_LETOFF: f32 = THETA_H_STRING * (1.0 - 0.001 / HAMMER_HEIGHT_FULL); // 0.39167
const THETA_H_BACKCHECK: f32 = THETA_H_STRING * (1.0 - 0.015 / HAMMER_HEIGHT_FULL); // 0.275

const LET_OFF_KEY_FRACTION: f32 = 0.95;
const RESET_KEY_FRACTION: f32 = 0.50;
const THETA_K_LETOFF: f32 = LET_OFF_KEY_FRACTION * THETA_K_MAX;
const THETA_K_RESET: f32 = RESET_KEY_FRACTION * THETA_K_MAX;
const TRANSMISSION_RATIO: f32 = THETA_H_STRING / THETA_K_LETOFF; // ≈ 9.4

// Linkage / contact model parameters (matched to the Python prototype).
const K_LINK: f32 = 200.0;
const D_LINK: f32 = 0.04;
const K_STRING: f32 = 4.0e3;
const D_STRING: f32 = 0.05;
const K_CHECK: f32 = 50.0;
const D_CHECK: f32 = 0.04;
const T_GRAVITY: f32 = M_HAMMER * G * HAMMER_CM_R; // ≈ 0.0118 N·m
const FRICTION_VEL_THRESHOLD: f32 = 0.05; // tanh smoothing scale, rad/s

// Engagement gate: don't run the simulation while the key is essentially at
// rest. ENGAGE > DISENGAGE provides hysteresis so a key hovering at the
// engage threshold doesn't flicker the simulation on and off each tick.
const SIM_ENGAGE_DEPTH: f32 = 0.005;
const SIM_DISENGAGE_DEPTH: f32 = 0.003;

/// Empirical calibration from `tools/key_recorder/piano_sim.py` over 19
/// recorded presses, then nudged ~20 % more sensitive based on hardware
/// playtesting (presses that felt forte were producing quiet MIDI).
/// 27.72 m/s would have placed the calibration peak at MIDI 127; we use
/// 23.10 m/s so the same press gets ~120 instead of ~100.
const HEAD_SPEED_FOR_MIDI_127: f32 = 23.10;

/// Channel pressure: stream the smoothed key depth post-strike so synths
/// that respect MPE pressure can inflect the held tone with finger weight.
const PRESSURE_SMOOTH_ALPHA: f32 = 0.30;
const PRESSURE_MIN_INTERVAL_MS: f32 = 5.0;

// Per-note CC64 sustain experiment. We optionally assert sustain on each
// note's own member channel: CC64=127 on strike. Whether we emit the
// matching CC64=0 on release depends on the master pedal state — if the
// spacebar (master CC64) is currently held, we leave the per-note sustain
// alone so the master pedal isn't fought by our per-note release. The
// per_note_sustain_enabled and master_sustain Arc<AtomicBool> live in
// ProfileConfig and are updated by the bridge.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SimState {
    Idle,        // depth below engage threshold; no sim running
    Engaged,     // jack-knuckle in contact, hammer follows key
    Escaped,     // jack let go; hammer in flight
    AfterStrike, // hammer rebounded off string
    Checked,     // hammer caught by back-check (key still pressed)
}

struct KeySim {
    state: SimState,
    /// Hammer angle (rad), counter-clockwise from rest.
    theta_h: f32,
    /// Hammer angular velocity (rad/s).
    omega_h: f32,
    /// Last reported depth, for hysteresis on the disengage threshold.
    last_depth: f32,
    /// Wall-clock timestamp of the previous tick (for variable-dt RK steps).
    last_t: Option<Instant>,
    /// MPE channel currently allocated for this key, if a note is sounding.
    channel: Option<u8>,
    /// MIDI note currently sounding (used for NoteOff on multi-strike).
    note_playing: Option<u8>,
    /// Smoothed depth for channel-pressure stream.
    pressure_smoothed: f32,
    /// Last-emitted pressure value (-1 sentinel = never).
    pressure_last_sent: i16,
    /// Wall-clock timestamp of the previous pressure emission.
    pressure_last_t: Option<Instant>,
}

impl KeySim {
    fn fresh() -> Self {
        Self {
            state: SimState::Idle,
            theta_h: THETA_H_REST,
            omega_h: 0.0,
            last_depth: 0.0,
            last_t: None,
            channel: None,
            note_playing: None,
            pressure_smoothed: 0.0,
            pressure_last_sent: -1,
            pressure_last_t: None,
        }
    }

    /// Reset to a clean rest state ready for the next press.
    fn reset_for_next_engagement(&mut self) {
        self.state = SimState::Idle;
        self.theta_h = THETA_H_REST;
        self.omega_h = 0.0;
        self.last_t = None;
        self.pressure_smoothed = 0.0;
        self.pressure_last_sent = -1;
        self.pressure_last_t = None;
    }
}

pub struct PianoSimProfile {
    config: ProfileConfig,
    sims: HashMap<u16, KeySim>,
    allocator: ChannelAllocator,
    priming_sent: bool,
}

impl PianoSimProfile {
    pub const META: ProfileMeta = ProfileMeta {
        name: "piano_sim",
        label: "Piano-key physics (Hirschkorn 1-DOF)",
        description:
            "Per-key hammer ODE driven by analog depth; multi-strike enabled, \
             velocity from simulated hammer head speed at string impact.",
    };

    pub fn new(config: ProfileConfig) -> Self {
        let allocator = ChannelAllocator::new(config.channel_low, config.channel_high);
        Self { config, sims: HashMap::new(), allocator, priming_sent: false }
    }

    /// Map hammer head speed (m/s) to MIDI velocity 1..127.
    fn head_speed_to_velocity(speed: f32) -> u8 {
        let clamped = speed.max(0.0).min(HEAD_SPEED_FOR_MIDI_127);
        let v = (127.0 * clamped / HEAD_SPEED_FOR_MIDI_127).round() as i32;
        v.clamp(1, 127) as u8
    }

    /// Right-hand side of the hammer ODE: returns dθ_h/dt and dω_h/dt.
    fn rhs(state: SimState, theta_k: f32, theta_h: f32, omega_h: f32) -> (f32, f32) {
        // Linkage torque, depending on the state machine.
        let t_link = match state {
            SimState::Engaged => {
                let target = TRANSMISSION_RATIO * theta_k;
                K_LINK * (target - theta_h) - D_LINK * omega_h
            }
            SimState::Checked => K_CHECK * (THETA_H_BACKCHECK - theta_h) - D_CHECK * omega_h,
            _ => 0.0,
        };

        // Gravity restoring toward rest.
        let t_grav = -T_GRAVITY * theta_h.cos();
        // Pivot friction (smoothed Coulomb).
        let t_fric = -ROT_FRICTION_HAMMER * (omega_h / FRICTION_VEL_THRESHOLD).tanh();
        // String contact (penalty spring + damping past the string angle).
        let t_str = if theta_h > THETA_H_STRING {
            -K_STRING * (theta_h - THETA_H_STRING) - D_STRING * omega_h
        } else {
            0.0
        };

        let t_total = t_link + t_grav + t_fric + t_str;
        (omega_h, t_total / I_HAMMER)
    }

    /// Single fixed-step RK4 integration over `dt` seconds.
    fn step(state: SimState, theta_k: f32, theta_h: f32, omega_h: f32, dt: f32) -> (f32, f32) {
        let (k1_th, k1_om) = Self::rhs(state, theta_k, theta_h, omega_h);
        let (k2_th, k2_om) = Self::rhs(
            state,
            theta_k,
            theta_h + 0.5 * dt * k1_th,
            omega_h + 0.5 * dt * k1_om,
        );
        let (k3_th, k3_om) = Self::rhs(
            state,
            theta_k,
            theta_h + 0.5 * dt * k2_th,
            omega_h + 0.5 * dt * k2_om,
        );
        let (k4_th, k4_om) = Self::rhs(
            state,
            theta_k,
            theta_h + dt * k3_th,
            omega_h + dt * k3_om,
        );
        (
            theta_h + dt * (k1_th + 2.0 * k2_th + 2.0 * k3_th + k4_th) / 6.0,
            omega_h + dt * (k1_om + 2.0 * k2_om + 2.0 * k3_om + k4_om) / 6.0,
        )
    }

    /// Step the per-key simulation forward by one polling tick. Returns any
    /// strike event detected during this step as a hammer head speed (m/s).
    /// Associated function so it can be called without holding `&mut self`
    /// (the caller already holds a `&mut KeySim` via `HashMap::entry`).
    fn advance(sim: &mut KeySim, depth: f32, now: Instant) -> Option<f32> {
        let theta_k = THETA_K_MAX * depth.clamp(0.0, 1.0);
        let dt = match sim.last_t {
            Some(prev) => {
                let d = now.duration_since(prev).as_secs_f32();
                // Cap dt so a long stall doesn't propagate huge ODE steps.
                d.min(0.005)
            }
            None => 1.0 / 8000.0,
        };
        sim.last_t = Some(now);

        // Update state machine.
        //
        // Let-off is triggered by HAMMER angle, not key angle: in a real
        // action, the jack's toe hits the let-off button when the hammer
        // is ~1 mm from the string, regardless of how fast the key got
        // there. Triggering on key angle (the previous bug) caused the
        // linkage to disengage before the hammer had built up momentum
        // on very fast presses, producing missed strikes ~3-5% of the
        // time.
        match sim.state {
            SimState::Engaged if sim.theta_h > THETA_H_LETOFF => sim.state = SimState::Escaped,
            SimState::AfterStrike if sim.theta_h <= THETA_H_BACKCHECK && theta_k > THETA_K_RESET => {
                sim.state = SimState::Checked;
            }
            SimState::AfterStrike | SimState::Checked if theta_k < THETA_K_RESET => {
                sim.state = SimState::Engaged;
            }
            _ => {}
        }

        // Substep the ODE if dt is much larger than expected (eg. first
        // sample after engagement). At the bridge's 8 kHz polling rate,
        // dt ≈ 125 µs and one RK4 step is plenty.
        let n_substeps = ((dt / 2.0e-4).ceil() as i32).max(1);
        let sub_dt = dt / n_substeps as f32;
        let mut theta_h = sim.theta_h;
        let mut omega_h = sim.omega_h;
        let mut strike: Option<f32> = None;
        for _ in 0..n_substeps {
            let prev_theta = theta_h;
            let (next_theta, next_omega) =
                Self::step(sim.state, theta_k, theta_h, omega_h, sub_dt);
            // Strike detection: hammer angle crossed the string going up.
            // We accept strikes from any non-rebound state — gating on
            // SimState::Escaped is wrong because the hammer can be driven
            // through the string by a stiff press while still in Engaged.
            if matches!(sim.state, SimState::Engaged | SimState::Escaped)
                && next_theta >= THETA_H_STRING
                && prev_theta < THETA_H_STRING
                && next_omega > 0.0
            {
                // Linearly interpolate the crossing for a slightly better
                // velocity estimate.
                let frac = (THETA_H_STRING - prev_theta) / (next_theta - prev_theta).max(1e-9);
                let omega_at_strike = omega_h + frac * (next_omega - omega_h);
                strike = Some(omega_at_strike);
                sim.state = SimState::AfterStrike;
                // Apply a coefficient of restitution for the rebound.
                theta_h = next_theta;
                omega_h = -0.45 * omega_at_strike;
            } else {
                theta_h = next_theta;
                omega_h = next_omega;
            }
        }
        sim.theta_h = theta_h;
        sim.omega_h = omega_h;
        sim.last_depth = depth;

        strike.map(|omega| omega * HAMMER_HEAD_R)
    }
}

impl InputProfile for PianoSimProfile {
    fn priming(&mut self) -> MidiBatch {
        let mut out = MidiBatch::new();
        if !self.priming_sent {
            let zone_size = self.config.channel_high - self.config.channel_low + 1;
            for m in mpe_configuration_zone(zone_size) {
                out.push(m);
            }
            self.priming_sent = true;
        }
        out
    }

    fn process(&mut self, key: KeyDescriptor, keycode: u16, depth: f32, now: Instant) -> MidiBatch {
        let mut out = MidiBatch::new();

        let sim = self.sims.entry(keycode).or_insert_with(KeySim::fresh);

        // Engagement gate (with hysteresis).
        if sim.state == SimState::Idle {
            if depth < SIM_ENGAGE_DEPTH {
                sim.last_depth = depth;
                return out;
            }
            sim.state = SimState::Engaged;
            sim.last_t = Some(now);
        } else if depth < SIM_DISENGAGE_DEPTH {
            // Key fully released — wrap up any sounding note and reset.
            // Only emit the per-note CC64=0 if the master pedal isn't
            // currently asserting sustain; otherwise the master pedal is
            // doing the sustain for this note and we mustn't fight it.
            let per_note_on = self
                .config
                .per_note_sustain_enabled
                .load(Ordering::Acquire);
            let master_on = self.config.master_sustain.load(Ordering::Acquire);
            if let Some(channel) = sim.channel {
                if per_note_on && !master_on {
                    out.push(control_change(channel, 0x40, 0));
                }
                if let Some(note) = sim.note_playing {
                    out.push(note_off(channel, note));
                }
                self.allocator.release(keycode, now);
            }
            sim.channel = None;
            sim.note_playing = None;
            sim.reset_for_next_engagement();
            return out;
        }

        // Step the physics.
        let strike_speed = Self::advance(sim, depth, now);

        // Emit MIDI for any strike event.
        if let Some(head_speed) = strike_speed {
            let velocity = Self::head_speed_to_velocity(head_speed);

            let channel = match sim.channel {
                Some(c) => c,
                None => {
                    let (c, stolen) = self.allocator.acquire(keycode, key.controller_note);
                    if let Some(ev) = stolen {
                        out.push(note_off(ev.channel, ev.note));
                    }
                    sim.channel = Some(c);
                    c
                }
            };

            // Multi-strike: NoteOff prior note (same channel), then NoteOn
            // the new strike. We deliberately do NOT emit the pre-note reset
            // triplet (CC74=0 / D=0 / PB=center) — the channel state should
            // persist across strikes so the synth's per-note expression
            // doesn't reset every time a string is rehit.
            if let Some(prev_note) = sim.note_playing {
                out.push(note_off(channel, prev_note));
            }
            out.push(note_on(channel, key.controller_note, velocity));
            sim.note_playing = Some(key.controller_note);

            // Per-note sustain on this channel — gated by the runtime
            // switch in ProfileConfig. The matching CC64=0 emission on
            // release is additionally gated by master-sustain state (see
            // the SIM_DISENGAGE_DEPTH branch).
            if self
                .config
                .per_note_sustain_enabled
                .load(Ordering::Acquire)
            {
                out.push(control_change(channel, 0x40, 127));
            }
        }

        // Stream channel pressure post-strike (the "finger-weight" feel).
        if self.config.aftertouch_enabled {
            if let (Some(channel), Some(_)) = (sim.channel, sim.note_playing) {
                sim.pressure_smoothed +=
                    PRESSURE_SMOOTH_ALPHA * (depth.clamp(0.0, 1.0) - sim.pressure_smoothed);
                let val = (sim.pressure_smoothed * 127.0).round() as i16;
                let throttle_ok = sim
                    .pressure_last_t
                    .map(|t| {
                        now.duration_since(t).as_secs_f32() * 1000.0
                            >= PRESSURE_MIN_INTERVAL_MS
                    })
                    .unwrap_or(true);
                if throttle_ok && (val - sim.pressure_last_sent).abs() >= 1 {
                    out.push(channel_pressure(channel, val.clamp(0, 127) as u8));
                    sim.pressure_last_sent = val;
                    sim.pressure_last_t = Some(now);
                }
            }
        }

        out
    }

    fn shutdown(&mut self) -> MidiBatch {
        let mut out = MidiBatch::new();
        let per_note_on = self
            .config
            .per_note_sustain_enabled
            .load(Ordering::Acquire);
        let master_on = self.config.master_sustain.load(Ordering::Acquire);
        for (channel, note, _kc) in self.allocator.iter_active() {
            if per_note_on && !master_on {
                out.push(control_change(channel, 0x40, 0));
            }
            out.push(note_off(channel, note));
        }
        let _ = self.allocator._force_release_all(Instant::now());
        for sim in self.sims.values_mut() {
            sim.channel = None;
            sim.note_playing = None;
            sim.reset_for_next_engagement();
        }
        out
    }

    fn meta(&self) -> ProfileMeta {
        Self::META
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn key(x: i16, y: i16, note: u8) -> KeyDescriptor {
        KeyDescriptor { logical_x: x, logical_y: y, controller_note: note }
    }

    fn run_press(press_steps: usize) -> u8 {
        let mut prof = PianoSimProfile::new(ProfileConfig::default());
        prof.priming();
        let kc: u16 = 0x1d;
        let key = key(0, 0, 60);
        let t0 = Instant::now();
        let step = Duration::from_micros(125);
        let total_steps = 1600usize.max(press_steps + 400);
        let mut velocity = 0u8;
        for i in 0..total_steps {
            let depth = if i < press_steps {
                (i as f32) / (press_steps as f32)
            } else {
                1.0
            };
            for m in prof.process(key, kc, depth, t0 + step * (i as u32)).iter() {
                if m[0] & 0xF0 == 0x90 && velocity == 0 {
                    velocity = m[2];
                }
            }
        }
        velocity
    }

    /// A fast press generates a higher MIDI velocity than a slow press.
    #[test]
    fn fast_press_louder_than_slow_press() {
        let fast_vel = run_press(80); // ~10 ms full press
        let slow_vel = run_press(1200); // ~150 ms full press
        assert!(
            fast_vel > slow_vel,
            "fast press velocity ({}) should exceed slow press velocity ({})",
            fast_vel,
            slow_vel
        );
    }

    /// Regression: a single-tick depth jump from rest to 1.0 must still
    /// produce a NoteOn. Earlier the let-off transition fired on key angle,
    /// so a press that crossed the 0.95 threshold in one tick disengaged
    /// the linkage before the hammer built up any velocity, producing a
    /// silent press ~3-5% of the time.
    #[test]
    fn instant_full_press_still_strikes() {
        for press_steps in [1usize, 2, 4, 8] {
            let mut prof = PianoSimProfile::new(ProfileConfig::default());
            prof.priming();
            let kc: u16 = 0x1d;
            let key = key(0, 0, 60);
            let t0 = Instant::now();
            let step = Duration::from_micros(125);
            let total_steps = 2000;
            let mut got_note_on = false;
            for i in 0..total_steps {
                let depth = if i < press_steps {
                    (i as f32 + 1.0) / (press_steps as f32)
                } else {
                    1.0
                };
                for m in prof.process(key, kc, depth, t0 + step * (i as u32)).iter() {
                    if m[0] & 0xF0 == 0x90 {
                        got_note_on = true;
                    }
                }
                if got_note_on {
                    break;
                }
            }
            assert!(
                got_note_on,
                "no NoteOn for instant press over {press_steps} tick(s)"
            );
        }
    }

    /// A partial release + repress with depth never below DISENGAGE produces
    /// a second NoteOff/NoteOn pair on the same channel without the MPE
    /// reset triplet.
    #[test]
    fn multi_strike_emits_pair_without_reset_triplet() {
        let mut prof = PianoSimProfile::new(ProfileConfig::default());
        prof.priming();
        let kc: u16 = 0x1d;
        let key = key(0, 0, 60);

        let t0 = Instant::now();
        let step = Duration::from_micros(125);
        let mut events = Vec::new();
        // Fast press 0 → 1.0 over 80 steps, hold 200, release to 0.4 over 80,
        // hold 80, press to 1.0 again over 80, hold 200.
        let traj: Vec<f32> = (0..80)
            .map(|i| (i as f32) / 80.0)
            .chain(std::iter::repeat(1.0).take(200))
            .chain((0..80).map(|i| 1.0 - 0.6 * (i as f32) / 80.0))
            .chain(std::iter::repeat(0.4).take(80))
            .chain((0..80).map(|i| 0.4 + 0.6 * (i as f32) / 80.0))
            .chain(std::iter::repeat(1.0).take(200))
            .collect();
        let mut t = t0;
        for d in traj {
            let batch = prof.process(key, kc, d, t);
            for m in batch.iter() {
                events.push(m.clone());
            }
            t += step;
        }

        // Count NoteOns and NoteOffs.
        let n_on = events.iter().filter(|m| m[0] & 0xF0 == 0x90).count();
        let n_off = events.iter().filter(|m| m[0] & 0xF0 == 0x80).count();
        assert!(n_on >= 2, "expected at least two NoteOns, got {n_on}");
        assert!(n_off >= 1, "expected at least one NoteOff between strikes");
        // No CC74 pre-note reset (PianoSimProfile must not emit the MPE reset
        // triplet around strikes).
        assert!(
            events.iter().all(|m| !(m[0] & 0xF0 == 0xB0 && m[1] == 0x4A)),
            "PianoSimProfile must not emit CC74 reset between strikes"
        );
    }

    /// With per_note_sustain enabled and master sustain off, full release
    /// emits CC64=0 just before the final NoteOff.
    #[test]
    fn per_note_sustain_release_when_master_off() {
        let cfg = ProfileConfig::default();
        cfg.per_note_sustain_enabled.store(true, Ordering::Release);
        let mut prof = PianoSimProfile::new(cfg);
        prof.priming();
        let kc: u16 = 0x1d;
        let key = key(0, 0, 60);

        let t0 = Instant::now();
        let step = Duration::from_micros(125);
        let mut events = Vec::new();
        for i in 0..200 {
            let d = ((i as f32) / 80.0).min(1.0);
            for m in prof.process(key, kc, d, t0 + step * i).iter() {
                events.push(m.clone());
            }
        }
        for m in prof.process(key, kc, 0.0, t0 + step * 250).iter() {
            events.push(m.clone());
        }

        let last_cc64 = events
            .iter()
            .rposition(|m| m[0] & 0xF0 == 0xB0 && m[1] == 0x40);
        let last_off = events.iter().rposition(|m| m[0] & 0xF0 == 0x80);
        assert!(last_cc64.is_some(), "expected a final CC64 message");
        assert!(last_off.is_some(), "expected a final NoteOff");
        assert!(last_cc64.unwrap() < last_off.unwrap());
        assert_eq!(events[last_cc64.unwrap()][2], 0);
    }

    /// With per_note_sustain enabled but master sustain currently asserted,
    /// the release CC64=0 must NOT be emitted (else it'd fight the master
    /// pedal). NoteOff still fires.
    #[test]
    fn per_note_sustain_suppressed_when_master_on() {
        let cfg = ProfileConfig::default();
        cfg.per_note_sustain_enabled.store(true, Ordering::Release);
        cfg.master_sustain.store(true, Ordering::Release);
        let mut prof = PianoSimProfile::new(cfg);
        prof.priming();
        let kc: u16 = 0x1d;
        let key = key(0, 0, 60);

        let t0 = Instant::now();
        let step = Duration::from_micros(125);
        let mut events = Vec::new();
        for i in 0..200 {
            let d = ((i as f32) / 80.0).min(1.0);
            for m in prof.process(key, kc, d, t0 + step * i).iter() {
                events.push(m.clone());
            }
        }
        for m in prof.process(key, kc, 0.0, t0 + step * 250).iter() {
            events.push(m.clone());
        }

        // No CC64=0 should appear after the strike's CC64=127.
        let cc64_zero = events
            .iter()
            .any(|m| m[0] & 0xF0 == 0xB0 && m[1] == 0x40 && m[2] == 0);
        assert!(
            !cc64_zero,
            "per-note CC64=0 must be suppressed while master sustain is held"
        );
        let any_off = events.iter().any(|m| m[0] & 0xF0 == 0x80);
        assert!(any_off, "NoteOff must still fire on full release");
    }

    /// Default (per_note_sustain disabled): no CC64 messages emitted at all.
    #[test]
    fn per_note_sustain_disabled_emits_no_cc64() {
        let mut prof = PianoSimProfile::new(ProfileConfig::default());
        prof.priming();
        let kc: u16 = 0x1d;
        let key = key(0, 0, 60);

        let t0 = Instant::now();
        let step = Duration::from_micros(125);
        let mut events = Vec::new();
        for i in 0..200 {
            let d = ((i as f32) / 80.0).min(1.0);
            for m in prof.process(key, kc, d, t0 + step * i).iter() {
                events.push(m.clone());
            }
        }
        for m in prof.process(key, kc, 0.0, t0 + step * 250).iter() {
            events.push(m.clone());
        }

        assert!(
            events.iter().all(|m| !(m[0] & 0xF0 == 0xB0 && m[1] == 0x40)),
            "no CC64 (sustain) should be emitted when the switch is off"
        );
    }
}
