use std::time::Instant;

use crate::midi::MidiBatch;

use super::{mpe::MpeProfile, InputProfile, KeyDescriptor, ProfileConfig, ProfileMeta};

/// Piano-key physics simulation profile.
///
/// **v1: stub.** Delegates to `MpeProfile`. The UI surface and profile selector
/// machinery exists end-to-end so the real physics model can be dropped in
/// later without touching anything else.
///
/// Roadmap for the real implementation:
///   - Per-key mass/spring/damper system whose displacement is driven by the
///     analog depth signal (acting as a position constraint via a stiff PD
///     coupling). The hammer is a separate body coupled to the key by a
///     let-off mechanism.
///   - Velocity emitted at the moment the hammer crosses a "let-off" position,
///     proportional to hammer impact velocity. This yields curves much closer
///     to a real piano than dual-threshold timing for slow / staccato playing.
///   - References: Hall, "The dynamics of piano keys"; Boutillon, "Model for
///     piano hammers"; Conklin, "Generation of partials due to nonlinear
///     mixing in a stringed instrument".
pub struct PianoSimProfile {
    inner: MpeProfile,
}

impl PianoSimProfile {
    pub const META: ProfileMeta = ProfileMeta {
        name: "piano_sim",
        label: "Piano-key physics (stub)",
        description: "Hammer-action simulation — currently delegates to MPE; real model TBD",
    };

    pub fn new(config: ProfileConfig) -> Self {
        Self {
            inner: MpeProfile::new(config),
        }
    }
}

impl InputProfile for PianoSimProfile {
    fn priming(&mut self) -> MidiBatch {
        self.inner.priming()
    }

    fn process(&mut self, key: KeyDescriptor, keycode: u16, depth: f32, now: Instant) -> MidiBatch {
        self.inner.process(key, keycode, depth, now)
    }

    fn shutdown(&mut self) -> MidiBatch {
        self.inner.shutdown()
    }

    fn meta(&self) -> ProfileMeta {
        Self::META
    }
}
