/// Per-key velocity finite state machine.
///
/// Strategy: dual-threshold timing. As the key descends:
///   - Crossing T_arm upward records t_arm.
///   - Crossing T_trigger upward emits a NoteOn. The press speed is estimated
///     by a least-squares slope fit over every (time, depth) sample collected
///     during the rise (from just above T_off up to the trigger crossing) and
///     converted to an equivalent arm→trigger dt; with fewer than 3 samples it
///     falls back to the raw t_arm→now endpoint dt. Velocity maps dt
///     log-linearly from [min_dt, max_dt] to [127, 1].
/// On release: depth dropping below T_off emits NoteOff. T_release defines the
/// retrigger window — depth must first cross below T_release before re-arming.
///
/// The slope fit matters most for polled (request/response) keyboards like the
/// MAD68HE, where a fast strike may yield only a handful of samples at uneven
/// timestamps: a regression over all of them is far less sensitive to the ±one
/// sample quantisation of the two threshold crossings than the endpoint dt.
///
/// All thresholds are configurable so the host can tune playability without
/// recompiling.

use std::time::Instant;

/// Max rise samples kept per key. At the Wooting's 8 kHz stream this window
/// still spans the fastest strikes; for polled boards it is never reached.
const MAX_SAMPLES: usize = 48;

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
    /// (time, depth) samples of the current rise, collected while depth is
    /// above T_off and the note hasn't fired yet. Cleared on full release.
    samples: Vec<(Instant, f32)>,
    config: VelocityConfig,
}

impl VelocityFsm {
    pub fn new(config: VelocityConfig) -> Self {
        Self {
            state: FsmState::Idle,
            t_arm: None,
            samples: Vec::new(),
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
            self.samples.clear();
            return if was_active {
                VelocityEvent::NoteOff
            } else {
                VelocityEvent::None
            };
        }

        // Record the rise for the slope fit while the note hasn't fired yet.
        if matches!(self.state, FsmState::Idle | FsmState::Armed) {
            if self.samples.len() >= MAX_SAMPLES {
                self.samples.remove(0);
            }
            self.samples.push((now, depth));
        }

        match self.state {
            FsmState::Idle => {
                if depth >= cfg.arm {
                    self.t_arm = Some(now);
                    self.state = FsmState::Armed;
                    if depth >= cfg.trigger {
                        // Skipped past arm in a single sample. The endpoint dt
                        // is unknowable (0 → max velocity); the slope fit can
                        // still recover the real speed from pre-arm samples.
                        self.state = FsmState::On;
                        let v = self.rise_velocity(0.0, sensitivity);
                        return VelocityEvent::NoteOn(v);
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
                    let v = self.rise_velocity(dt_ms, sensitivity);
                    return VelocityEvent::NoteOn(v);
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
        self.samples.clear();
    }

    /// Velocity at the trigger crossing: least-squares slope over the rise
    /// samples, expressed as the equivalent arm→trigger dt so the existing
    /// log-linear dt→velocity mapping (and the sensitivity semantics) are
    /// unchanged. Falls back to `endpoint_dt_ms` when the fit is degenerate.
    fn rise_velocity(&self, endpoint_dt_ms: f32, sensitivity: f32) -> u8 {
        let cfg = &self.config;
        // Only fit the recent, relevant part of the rise: samples within a
        // bounded look-back (so a long dwell below arm doesn't dilute a fast
        // final strike) and at or below the trigger depth.
        let window_ms = cfg.max_dt_ms * 2.0;
        let newest = match self.samples.last() {
            Some(&(t, _)) => t,
            None => return self.compute_velocity(endpoint_dt_ms, sensitivity),
        };
        let mut n = 0.0f32;
        let (mut sx, mut sy, mut sxx, mut sxy) = (0.0f32, 0.0f32, 0.0f32, 0.0f32);
        for &(t, d) in &self.samples {
            let age_ms = newest.duration_since(t).as_secs_f32() * 1000.0;
            if age_ms > window_ms || d > cfg.trigger + 0.02 {
                continue;
            }
            let x = -age_ms; // time relative to newest sample, in ms
            n += 1.0;
            sx += x;
            sy += d;
            sxx += x * x;
            sxy += x * d;
        }
        if n >= 3.0 {
            let denom = n * sxx - sx * sx;
            if denom > f32::EPSILON {
                let slope = (n * sxy - sx * sy) / denom; // depth units per ms
                if slope > 1e-4 {
                    let dt_equiv = (cfg.trigger - cfg.arm) / slope;
                    return self.compute_velocity(dt_equiv, sensitivity);
                }
            }
        }
        self.compute_velocity(endpoint_dt_ms, sensitivity)
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
    fn slope_fit_uses_dense_samples() {
        // A fast rise sampled densely: the fit should see the true slope and
        // produce a high velocity even though the arm->trigger endpoint pair
        // alone would say the same — and, crucially, a slow dense rise must
        // stay quiet.
        let mut fast = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        for i in 0..6u64 {
            // 0.11 depth per 0.5 ms => trigger (0.5) reached ~2.3 ms after arm.
            let d = 0.05 + 0.11 * i as f32;
            let ev = fast.update(d, t0 + Duration::from_micros(i * 500), 1.0);
            if let VelocityEvent::NoteOn(v) = ev {
                assert!(v >= 100, "dense fast rise should be loud, got {v}");
                return;
            }
        }
        panic!("fast rise never triggered");
    }

    #[test]
    fn slope_fit_slow_dense_rise_is_quiet() {
        let mut slow = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        for i in 0..40u64 {
            // 0.0125 depth per 2 ms => ~56 ms arm->trigger.
            let d = 0.05 + 0.0125 * i as f32;
            let ev = slow.update(d, t0 + Duration::from_millis(i * 2), 1.0);
            if let VelocityEvent::NoteOn(v) = ev {
                assert!(v <= 30, "dense slow rise should be quiet, got {v}");
                return;
            }
        }
        panic!("slow rise never triggered");
    }

    #[test]
    fn idle_below_arm_does_nothing() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let _ = at(0);
        assert_eq!(fsm.update(0.05, Instant::now(), 1.0), VelocityEvent::None);
        assert_eq!(fsm.update(0.10, Instant::now(), 1.0), VelocityEvent::None);
    }
}
