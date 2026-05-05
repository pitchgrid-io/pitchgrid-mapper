use std::sync::atomic::AtomicBool;
use std::sync::Arc;
use std::time::Instant;

use crate::channels::ChannelAllocator;
use crate::midi::MidiBatch;
use crate::velocity::VelocityConfig;

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
