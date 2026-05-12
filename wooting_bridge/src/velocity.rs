/// Per-key velocity finite state machine.
///
/// Strategy: dual-threshold timing. As the key descends:
///   - Crossing T_arm upward records t_arm.
///   - Crossing T_trigger upward computes dt = now - t_arm and emits a NoteOn.
///     Velocity is dt mapped log-linearly from [min_dt, max_dt] to [127, 1].
/// On release: depth dropping below T_off emits NoteOff. T_release defines the
/// retrigger window — depth must first cross below T_release before re-arming.
///
/// All thresholds are configurable so the host can tune playability without
/// recompiling.

use std::time::Instant;

#[derive(Debug, Clone, Copy)]
pub struct VelocityConfig {
    pub arm: f32,
    pub trigger: f32,
    pub release: f32,
    pub off: f32,
    pub min_dt_ms: f32,
    pub max_dt_ms: f32,
}

impl Default for VelocityConfig {
    fn default() -> Self {
        Self {
            arm: 0.15,
            trigger: 0.50,
            release: 0.30,
            off: 0.10,
            min_dt_ms: 2.0,
            max_dt_ms: 80.0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FsmState {
    Idle,
    Armed,
    On,
    Releasing,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VelocityEvent {
    NoteOn(u8),
    NoteOff,
    None,
}

#[derive(Debug)]
pub struct VelocityFsm {
    state: FsmState,
    t_arm: Option<Instant>,
    config: VelocityConfig,
}

impl VelocityFsm {
    pub fn new(config: VelocityConfig) -> Self {
        Self {
            state: FsmState::Idle,
            t_arm: None,
            config,
        }
    }

    /// Feed a fresh depth sample. `sensitivity` reshapes the curve by
    /// scaling the effective time-to-trigger (lower sens = faster press
    /// required to reach the same velocity). Full 1..=127 range is
    /// reachable at any positive sensitivity if the press is fast enough.
    pub fn update(&mut self, depth: f32, now: Instant, sensitivity: f32) -> VelocityEvent {
        let cfg = &self.config;

        // Full release short-circuits from any state — drives the only NoteOff
        // path and resets the FSM.
        if depth < cfg.off {
            let was_active = matches!(self.state, FsmState::On | FsmState::Releasing);
            self.state = FsmState::Idle;
            self.t_arm = None;
            return if was_active {
                VelocityEvent::NoteOff
            } else {
                VelocityEvent::None
            };
        }

        match self.state {
            FsmState::Idle => {
                if depth >= cfg.arm {
                    self.t_arm = Some(now);
                    self.state = FsmState::Armed;
                    if depth >= cfg.trigger {
                        // Skipped past arm in a single sample — emit immediately at min dt.
                        self.state = FsmState::On;
                        return VelocityEvent::NoteOn(self.compute_velocity(0.0, sensitivity));
                    }
                }
                VelocityEvent::None
            }
            FsmState::Armed => {
                if depth >= cfg.trigger {
                    let dt_ms = self
                        .t_arm
                        .map(|t| now.duration_since(t).as_secs_f32() * 1000.0)
                        .unwrap_or(0.0);
                    self.state = FsmState::On;
                    return VelocityEvent::NoteOn(self.compute_velocity(dt_ms, sensitivity));
                }
                VelocityEvent::None
            }
            FsmState::On => {
                if depth < cfg.release {
                    self.state = FsmState::Releasing;
                }
                VelocityEvent::None
            }
            FsmState::Releasing => {
                // No retrigger from a partial release. The note keeps sounding
                // on its existing channel until depth crosses below T_off
                // (handled by the short-circuit above). If depth recovers above
                // T_release, slip back to On silently so further partial
                // releases can still drive future Releasing transitions.
                if depth >= cfg.release {
                    self.state = FsmState::On;
                }
                VelocityEvent::None
            }
        }
    }

    pub fn is_active(&self) -> bool {
        matches!(self.state, FsmState::On | FsmState::Releasing)
    }

    pub fn force_off(&mut self) {
        self.state = FsmState::Idle;
        self.t_arm = None;
    }

    fn compute_velocity(&self, dt_ms: f32, sensitivity: f32) -> u8 {
        let cfg = &self.config;
        // Sensitivity scales the *speed required to reach 127*. At sens<1
        // the effective dt is longer (you need to be faster for the same
        // velocity); at sens>1 it's shorter (slower presses get loud).
        // Either way 127 is always reachable for a press fast enough that
        // dt/sens <= min_dt_ms.
        let sens = sensitivity.max(0.01);
        let effective_dt = dt_ms / sens;
        let clamped = effective_dt.clamp(cfg.min_dt_ms, cfg.max_dt_ms);
        // Log-linear from min_dt -> 127 down to max_dt -> 1.
        let t = (clamped.ln() - cfg.min_dt_ms.ln())
            / (cfg.max_dt_ms.ln() - cfg.min_dt_ms.ln());
        let v = 127.0 - t * 126.0;
        v.round().clamp(1.0, 127.0) as u8
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn at(ms: u64) -> Instant {
        Instant::now() + Duration::from_millis(ms)
    }

    #[test]
    fn fast_press_yields_high_velocity() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        assert_eq!(fsm.update(0.0, t0, 1.0), VelocityEvent::None);
        assert_eq!(fsm.update(0.20, t0 + Duration::from_millis(0), 1.0), VelocityEvent::None);
        let ev = fsm.update(0.55, t0 + Duration::from_millis(2), 1.0);
        match ev {
            VelocityEvent::NoteOn(v) => assert!(v >= 90, "fast press should be loud, got {}", v),
            _ => panic!("expected NoteOn, got {:?}", ev),
        }
    }

    #[test]
    fn slow_press_yields_low_velocity() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        fsm.update(0.0, t0, 1.0);
        fsm.update(0.20, t0 + Duration::from_millis(0), 1.0);
        let ev = fsm.update(0.55, t0 + Duration::from_millis(80), 1.0);
        match ev {
            VelocityEvent::NoteOn(v) => assert!(v <= 5, "slow press should be quiet, got {}", v),
            _ => panic!("expected NoteOn"),
        }
    }

    #[test]
    fn release_below_off_emits_note_off() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        fsm.update(0.20, t0, 1.0);
        fsm.update(0.55, t0 + Duration::from_millis(5), 1.0);
        assert_eq!(fsm.update(0.20, t0 + Duration::from_millis(50), 1.0), VelocityEvent::None); // -> Releasing
        assert_eq!(fsm.update(0.05, t0 + Duration::from_millis(80), 1.0), VelocityEvent::NoteOff);
    }

    #[test]
    fn partial_release_then_repress_does_not_retrigger() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        fsm.update(0.20, t0, 1.0);
        fsm.update(0.55, t0 + Duration::from_millis(5), 1.0); // NoteOn
        fsm.update(0.20, t0 + Duration::from_millis(40), 1.0); // -> Releasing
        // Push back deeper without ever crossing T_off — no new NoteOn.
        let ev = fsm.update(0.60, t0 + Duration::from_millis(60), 1.0);
        assert_eq!(ev, VelocityEvent::None, "partial-release+repress must not retrigger");
        assert!(fsm.is_active(), "key must remain active");
    }

    #[test]
    fn full_release_then_press_does_retrigger() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        fsm.update(0.20, t0, 1.0);
        fsm.update(0.55, t0 + Duration::from_millis(5), 1.0); // NoteOn
        fsm.update(0.05, t0 + Duration::from_millis(40), 1.0); // through Releasing -> Idle, NoteOff
        // Real release happened. Now a fresh press should produce a new NoteOn.
        fsm.update(0.20, t0 + Duration::from_millis(80), 1.0);
        let ev = fsm.update(0.55, t0 + Duration::from_millis(85), 1.0);
        assert!(matches!(ev, VelocityEvent::NoteOn(_)), "fully-released key must retrigger, got {:?}", ev);
    }

    #[test]
    fn idle_below_arm_does_nothing() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let _ = at(0);
        assert_eq!(fsm.update(0.05, Instant::now(), 1.0), VelocityEvent::None);
        assert_eq!(fsm.update(0.10, Instant::now(), 1.0), VelocityEvent::None);
    }
}
