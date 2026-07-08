use std::collections::HashMap;
use std::time::Instant;

use smallvec::SmallVec;

use crate::channels::ChannelAllocator;
use crate::midi::{
    channel_pressure, control_change, mpe_configuration_zone, note_off, note_on, pitch_bend_center,
    MidiBatch,
};
use crate::velocity::{VelocityEvent, VelocityFsm};

use super::{read_sensitivity, InputProfile, KeyDescriptor, ProfileConfig, ProfileMeta};

/// Per-channel pressure smoother + transmit throttle.
#[derive(Debug, Clone, Copy)]
struct PressureState {
    smoothed: f32,
    last_sent: i16, // -1 means never sent
    last_send_at: Option<Instant>,
}

impl Default for PressureState {
    fn default() -> Self {
        Self {
            smoothed: 0.0,
            last_sent: -1,
            last_send_at: None,
        }
    }
}

pub struct MpeProfile {
    config: ProfileConfig,
    fsms: HashMap<u16, VelocityFsm>,
    allocator: ChannelAllocator,
    pressure: HashMap<u8, PressureState>, // keyed by channel
    priming_sent: bool,
}

impl MpeProfile {
    pub const META: ProfileMeta = ProfileMeta {
        name: "mpe",
        label: "MPE with channel pressure",
        description: "Each note on its own channel with post-attack pressure from key depth",
    };

    pub fn new(config: ProfileConfig) -> Self {
        let allocator = ChannelAllocator::new(config.channel_low, config.channel_high);
        Self {
            config,
            fsms: HashMap::new(),
            allocator,
            pressure: HashMap::new(),
            priming_sent: false,
        }
    }

    /// Pre-note channel reset: CC74=0, channel pressure=0, pitch bend = center.
    /// Called immediately before each NoteOn so the new note never inherits stale state.
    fn channel_reset(channel: u8) -> SmallVec<[crate::midi::MidiBytes; 8]> {
        let mut out = SmallVec::new();
        out.push(control_change(channel, 0x4A, 0x00)); // Timbre (CC74)
        out.push(channel_pressure(channel, 0));
        out.push(pitch_bend_center(channel));
        out
    }
}

impl InputProfile for MpeProfile {
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

        let sens = read_sensitivity(&self.config.sensitivity);
        let fsm = self
            .fsms
            .entry(keycode)
            .or_insert_with(|| VelocityFsm::new(self.config.velocity));
        let event = fsm.update(depth, now, sens);
        // Fire diagnostics (samples used, span, fire reason) — Some only when
        // the update above fired a NoteOn; exported for host-side analysis.
        let fire_stats = fsm.take_fire_stats();

        match event {
            VelocityEvent::NoteOn(velocity) => {
                // Sensitivity has already been baked into the velocity by the
                // FSM via dt scaling — full 1..=127 range is always reachable
                // for a fast-enough press at any positive sensitivity.
                // Allocate a channel; if we steal an active one, emit its NoteOff first.
                let (channel, stolen) = self.allocator.acquire(keycode, key.controller_note);
                if let Some(ev) = stolen {
                    out.push(note_off(ev.channel, ev.note));
                    self.pressure.remove(&ev.channel);
                }
                for m in Self::channel_reset(channel) {
                    out.push(m);
                }
                out.push(note_on(channel, key.controller_note, velocity));
                self.pressure.insert(channel, PressureState::default());
                if let Some(stats) = fire_stats {
                    self.config
                        .velocity_stats
                        .push((key.controller_note, keycode, stats));
                }
            }
            VelocityEvent::NoteOff => {
                if let Some((channel, note)) = self.allocator.release(keycode, now) {
                    out.push(note_off(channel, note));
                    self.pressure.remove(&channel);
                }
            }
            VelocityEvent::None => {
                // Continuous: stream channel pressure if a note is active for this key.
                if self.config.aftertouch_enabled
                    && fsm.is_active()
                {
                    if let Some(channel) = self.allocator.channel_for_keycode(keycode) {
                        let state = self.pressure.entry(channel).or_default();
                        state.smoothed += self.config.aftertouch_smooth_alpha
                            * (depth - state.smoothed);
                        let val = (state.smoothed.clamp(0.0, 1.0) * 127.0).round() as i16;
                        let throttle_ok = state
                            .last_send_at
                            .map(|t| {
                                now.duration_since(t).as_secs_f32() * 1000.0
                                    >= self.config.aftertouch_min_interval_ms
                            })
                            .unwrap_or(true);
                        if throttle_ok && (val - state.last_sent).abs() >= 1 {
                            out.push(channel_pressure(channel, val.clamp(0, 127) as u8));
                            state.last_sent = val;
                            state.last_send_at = Some(now);
                        }
                    }
                }
            }
        }
        out
    }

    fn shutdown(&mut self) -> MidiBatch {
        let mut out = MidiBatch::new();
        for (channel, note, _kc) in self.allocator.iter_active() {
            out.push(note_off(channel, note));
        }
        let _ = self.allocator._force_release_all(Instant::now());
        for fsm in self.fsms.values_mut() {
            fsm.force_off();
        }
        self.pressure.clear();
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
        KeyDescriptor {
            logical_x: x,
            logical_y: y,
            controller_note: note,
        }
    }

    #[test]
    fn note_on_emits_pre_note_reset_then_note_on() {
        let mut p = MpeProfile::new(ProfileConfig::default());
        let _ = p.priming();
        let t0 = Instant::now();
        let _ = p.process(key(0, 0, 60), 0x1d, 0.20, t0);
        let batch = p.process(key(0, 0, 60), 0x1d, 0.55, t0 + Duration::from_millis(2));
        // Expect: CC74=0, ChPressure=0, PitchBendCenter, NoteOn (4 messages).
        assert_eq!(batch.len(), 4);
        assert_eq!(batch[0][1], 0x4A); // CC74
        assert_eq!(batch[0][2], 0x00);
        assert_eq!(batch[1][0] & 0xF0, 0xD0); // channel pressure
        assert_eq!(batch[1][1], 0x00);
        assert_eq!(batch[2][0] & 0xF0, 0xE0); // pitch bend
        assert_eq!(batch[2][1], 0x00);
        assert_eq!(batch[2][2], 0x40);
        assert_eq!(batch[3][0] & 0xF0, 0x90); // note on
        assert_eq!(batch[3][1], 60);
    }

    #[test]
    fn release_emits_note_off() {
        let mut p = MpeProfile::new(ProfileConfig::default());
        let t0 = Instant::now();
        p.process(key(0, 0, 60), 0x1d, 0.20, t0);
        p.process(key(0, 0, 60), 0x1d, 0.55, t0 + Duration::from_millis(2));
        p.process(key(0, 0, 60), 0x1d, 0.20, t0 + Duration::from_millis(50)); // -> Releasing
        let batch = p.process(key(0, 0, 60), 0x1d, 0.05, t0 + Duration::from_millis(80));
        assert_eq!(batch.len(), 1);
        assert_eq!(batch[0][0] & 0xF0, 0x80); // note off
        assert_eq!(batch[0][1], 60);
    }

    #[test]
    fn priming_sends_mpe_configuration_once() {
        let mut p = MpeProfile::new(ProfileConfig::default());
        let first = p.priming();
        let second = p.priming();
        assert_eq!(first.len(), 3); // RPN MSB, RPN LSB, data
        assert_eq!(second.len(), 0);
    }

    #[test]
    fn shutdown_emits_note_offs_for_all_active() {
        let mut p = MpeProfile::new(ProfileConfig::default());
        let t0 = Instant::now();
        p.process(key(0, 0, 60), 0x1d, 0.20, t0);
        p.process(key(0, 0, 60), 0x1d, 0.55, t0 + Duration::from_millis(2));
        p.process(key(1, 0, 61), 0x1b, 0.20, t0 + Duration::from_millis(5));
        p.process(key(1, 0, 61), 0x1b, 0.55, t0 + Duration::from_millis(7));
        let off = p.shutdown();
        assert_eq!(off.len(), 2);
        assert!(off.iter().all(|m| m[0] & 0xF0 == 0x80));
    }

    #[test]
    fn sensitivity_shapes_note_on_velocity() {
        // With input-scaling sensitivity, the same dt at higher sens gives
        // a higher velocity (effective dt is shorter) and vice versa. The
        // full 1..=127 range remains reachable at any positive sensitivity.
        let cfg_lo = ProfileConfig::default();
        super::super::store_sensitivity(&cfg_lo.sensitivity, 0.5);
        let mut p_lo = MpeProfile::new(cfg_lo);
        let cfg_hi = ProfileConfig::default();
        super::super::store_sensitivity(&cfg_hi.sensitivity, 1.5);
        let mut p_hi = MpeProfile::new(cfg_hi);

        let t0 = Instant::now();
        let k = key(0, 0, 60);
        let press = |p: &mut MpeProfile| {
            p.process(k, 0x1d, 0.20, t0);
            let b = p.process(k, 0x1d, 0.55, t0 + Duration::from_millis(8));
            b.iter().find(|m| m[0] & 0xF0 == 0x90).map(|m| m[2]).unwrap_or(0)
        };
        let lo = press(&mut p_lo);
        let hi = press(&mut p_hi);
        assert!(hi > lo, "sensitivity 1.5 ({hi}) should exceed 0.5 ({lo})");

        // And: at low sensitivity a fast-enough press still reaches 127.
        let cfg_very_lo = ProfileConfig::default();
        super::super::store_sensitivity(&cfg_very_lo.sensitivity, 0.3);
        let mut p_vl = MpeProfile::new(cfg_very_lo);
        p_vl.process(k, 0x1d, 0.20, t0);
        // dt = 0.3 ms => effective dt = 1 ms < min_dt_ms (2ms) => v = 127.
        let evt = p_vl.process(k, 0x1d, 0.55, t0 + Duration::from_micros(300));
        let v = evt
            .iter()
            .find(|m| m[0] & 0xF0 == 0x90)
            .map(|m| m[2])
            .unwrap_or(0);
        assert_eq!(v, 127, "max velocity should be reachable even at low sens");
    }

    #[test]
    fn channel_pressure_throttled() {
        let mut cfg = ProfileConfig::default();
        cfg.aftertouch_min_interval_ms = 10.0;
        let mut p = MpeProfile::new(cfg);
        let t0 = Instant::now();
        p.process(key(0, 0, 60), 0x1d, 0.20, t0);
        p.process(key(0, 0, 60), 0x1d, 0.55, t0 + Duration::from_millis(2));
        // Continuous holds at varying depth — first should emit, immediate-next should not.
        let a = p.process(key(0, 0, 60), 0x1d, 0.60, t0 + Duration::from_millis(3));
        let b = p.process(key(0, 0, 60), 0x1d, 0.62, t0 + Duration::from_millis(4));
        assert!(a.iter().any(|m| m[0] & 0xF0 == 0xD0));
        assert!(b.iter().all(|m| m[0] & 0xF0 != 0xD0), "throttle should suppress immediate second");
    }
}
