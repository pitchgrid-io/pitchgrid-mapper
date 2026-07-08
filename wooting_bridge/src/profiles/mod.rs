use std::sync::atomic::{AtomicBool, AtomicU32};
use std::sync::Arc;
use std::time::Instant;

use crossbeam_queue::SegQueue;

use crate::channels::ChannelAllocator;
use crate::midi::MidiBatch;
use crate::velocity::{FireStats, VelocityConfig};

/// One velocity-diagnostics record per NoteOn: (controller note, HID keycode,
/// what the velocity was computed from). Drained by the host for analysis.
pub type VelocityStatsQueue = SegQueue<(u8, u16, FireStats)>;

pub mod mpe;
pub mod piano_sim;

/// One YAML-driven keymap entry: HID keycode -> (logical (x,y), controller MIDI note).
/// The controller note is the same value any other MIDI controller would send for
/// this physical key — the host's reverse-mapping table converts it back to (x,y).
///
/// Logical coords are i16 because controllers like the 60HE use negative x for
/// rows shifted left by `RowOffsets`.
#[derive(Debug, Clone, Copy)]
pub struct KeyDescriptor {
    pub logical_x: i16,
    pub logical_y: i16,
    pub controller_note: u8,
}

#[derive(Debug, Clone)]
pub struct ProfileConfig {
    pub velocity: VelocityConfig,
    pub channel_low: u8,
    pub channel_high: u8,
    pub aftertouch_enabled: bool,
    pub aftertouch_smooth_alpha: f32,
    pub aftertouch_min_interval_ms: f32,
    /// Live master-sustain state, updated by the bridge poll thread when the
    /// spacebar pedal flips. Profiles read this to decide whether their own
    /// per-note sustain release should fire.
    pub master_sustain: Arc<AtomicBool>,
    /// Whether profiles should emit per-note CC64 messages on member
    /// channels. When enabled, profiles emit CC64=127 on strike, but only
    /// emit CC64=0 on release if master sustain is also off (so the
    /// per-note signal never fights the master pedal).
    pub per_note_sustain_enabled: Arc<AtomicBool>,
    /// Output-velocity sensitivity multiplier, runtime-tunable. 1.0 leaves
    /// each profile's natural velocity curve intact; >1.0 increases the
    /// loudness of a given press; <1.0 quiets it. Profiles read this when
    /// emitting NoteOn and apply it as a final scaling on the computed
    /// MIDI velocity (clamped to 1..=127). Stored as the bits of an f32 in
    /// an AtomicU32 so reads on the hot path are lock-free.
    pub sensitivity: Arc<AtomicU32>,
    /// Fire-diagnostics sink: profiles that use the VelocityFsm push one
    /// record per NoteOn. Lock-free; only ever written on note onset.
    pub velocity_stats: Arc<VelocityStatsQueue>,
}

/// Read the current sensitivity factor (>= 0). Defaults to 1.0.
pub fn read_sensitivity(s: &AtomicU32) -> f32 {
    let bits = s.load(std::sync::atomic::Ordering::Acquire);
    let v = f32::from_bits(bits);
    if v.is_finite() && v >= 0.0 { v } else { 1.0 }
}

pub fn store_sensitivity(s: &AtomicU32, value: f32) {
    let v = value.clamp(0.0, 8.0);
    s.store(v.to_bits(), std::sync::atomic::Ordering::Release);
}

/// Apply sensitivity to a raw MIDI velocity. Clamps to 1..=127; returns 0
/// is never produced (would be NoteOff in disguise).
pub fn scale_velocity(raw: u8, sensitivity: f32) -> u8 {
    let scaled = (raw as f32 * sensitivity).round() as i32;
    scaled.clamp(1, 127) as u8
}

impl Default for ProfileConfig {
    fn default() -> Self {
        Self {
            velocity: VelocityConfig::default(),
            channel_low: 1,  // 0-indexed; MPE member channels are 1..=15
            channel_high: 15,
            aftertouch_enabled: true,
            aftertouch_smooth_alpha: 0.30,
            aftertouch_min_interval_ms: 5.0,
            master_sustain: Arc::new(AtomicBool::new(false)),
            per_note_sustain_enabled: Arc::new(AtomicBool::new(false)),
            sensitivity: Arc::new(AtomicU32::new((1.0f32).to_bits())),
            velocity_stats: Arc::new(SegQueue::new()),
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct ProfileMeta {
    pub name: &'static str,
    pub label: &'static str,
    pub description: &'static str,
}

pub trait InputProfile: Send {
    /// Process a single (keycode, depth) sample. Emit zero or more outgoing
    /// MIDI byte messages to be injected into the host MIDI input pipeline.
    fn process(&mut self, key: KeyDescriptor, keycode: u16, depth: f32, now: Instant) -> MidiBatch;

    /// Called when the profile is being deactivated. Should emit NoteOffs for
    /// any held notes so downstream hardware doesn't get stuck.
    fn shutdown(&mut self) -> MidiBatch;

    /// Called when the profile is freshly activated. Returns any priming MIDI
    /// (e.g. MPE Configuration RPN).
    fn priming(&mut self) -> MidiBatch {
        MidiBatch::new()
    }

    fn meta(&self) -> ProfileMeta;
}

pub fn registry() -> Vec<ProfileMeta> {
    vec![mpe::MpeProfile::META, piano_sim::PianoSimProfile::META]
}

pub fn build(name: &str, config: ProfileConfig) -> Option<Box<dyn InputProfile>> {
    match name {
        "mpe" => Some(Box::new(mpe::MpeProfile::new(config))),
        "piano_sim" => Some(Box::new(piano_sim::PianoSimProfile::new(config))),
        _ => None,
    }
}
