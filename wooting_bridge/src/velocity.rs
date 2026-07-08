/// Per-key velocity finite state machine.
///
/// A press is measured inside a bounded **detection window** that opens on the
/// first upward key movement:
///   - If depth crosses T_trigger before the window elapses, the note fires
///     immediately (a fast strike never waits).
///   - Otherwise, `max_detect_ms` after the window opened (default 10 ms —
///     roughly the hammer flight of an acoustic piano) the note fires with the
///     velocity the rise earned so far. If fewer than 2 samples have arrived
///     by then, the window extends until the second sample (two points is the
///     minimum that defines a speed).
///
/// Velocity is a least-squares slope fit over every (time, depth) sample of
/// the rise, converted to the equivalent arm→trigger dt and mapped
/// log-linearly from [min_dt, max_dt] to [127, 1]. With fewer than 2 usable
/// samples it falls back to the t_arm→trigger endpoint dt. The fit matters
/// most for polled (request/response) keyboards like the MAD68HE, where a
/// fast strike yields a handful of samples at uneven timestamps.
///
/// Release: depth below T_off emits NoteOff — except for notes that fired
/// shallower than T_off (window fire on a partial press), which release at
/// the near-zero floor instead so they aren't cut on the next sample.
/// Window-opening requires *upward movement*, so the slow crawl of a
/// releasing key can never ghost-fire a fresh note.
///
/// `max_detect_ms <= 0` disables the window entirely (trigger-crossing only —
/// the pre-window behaviour, and the default for Wooting boards where a
/// partial press has never sounded).
///
/// All thresholds are configurable so the host can tune playability without
/// recompiling.

use std::time::Instant;

/// Max rise samples kept per key. At the Madlions' ~1-3 kHz rising-phase poll
/// rate this comfortably spans the detection window; at the Wooting's 8 kHz
/// stream it holds the most recent ~6 ms, plenty for a trigger-crossing fit.
const MAX_SAMPLES: usize = 48;

/// Below this depth a key counts as truly released (the Madlions NKRO onset
/// actuation sits at ~0.029; an explicit zero sample is fed on NKRO release).
const RELEASE_FLOOR: f32 = 0.02;

/// Minimum upward depth change that counts as "movement" for opening a
/// detection window (~3 raw travel units — above resting sensor noise).
const MOVE_EPS: f32 = 0.008;

/// Minimum fit slope (depth/ms) for a window fire. Slower than this is a
/// resting finger, not a press: the window is cancelled and re-opens on the
/// next real movement. (For scale: velocity 1 maps to ~0.004 depth/ms.)
const MIN_SLOPE: f32 = 0.0005;

#[derive(Debug, Clone, Copy)]
pub struct VelocityConfig {
    pub arm: f32,
    pub trigger: f32,
    pub release: f32,
    pub off: f32,
    pub min_dt_ms: f32,
    pub max_dt_ms: f32,
    /// Detection-window length in ms (0 disables the window).
    pub max_detect_ms: f32,
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
            max_detect_ms: 10.0,
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

/// Diagnostics captured at the instant a NoteOn fires: what the velocity
/// estimate was actually computed from. Retrieved via `take_fire_stats()`.
#[derive(Debug, Clone, Copy)]
pub struct FireStats {
    pub velocity: u8,
    /// Rise samples in the buffer at fire time.
    pub n_samples: usize,
    /// Depth of the first and last buffered sample (normalised 0..=1).
    pub first_depth: f32,
    pub last_depth: f32,
    /// Time spanned by the buffered samples, ms.
    pub span_ms: f32,
    /// true = detection-window deadline fire; false = trigger crossing.
    pub window_fired: bool,
}

#[derive(Debug)]
pub struct VelocityFsm {
    state: FsmState,
    t_arm: Option<Instant>,
    /// Detection window: opened by the first upward movement of a fresh rise.
    t_window: Option<Instant>,
    /// (time, depth) samples of the current rise (window open, pre-note).
    samples: Vec<(Instant, f32)>,
    /// Previous sample depth — movement detection for window opening.
    prev_depth: f32,
    /// Note fired below T_off (partial-press window fire): release at the
    /// floor instead of T_off until the key has actually gone past T_off.
    shallow: bool,
    /// Diagnostics of the most recent NoteOn (consumed by take_fire_stats).
    last_fire: Option<FireStats>,
    config: VelocityConfig,
}

impl VelocityFsm {
    pub fn new(config: VelocityConfig) -> Self {
        Self {
            state: FsmState::Idle,
            t_arm: None,
            t_window: None,
            samples: Vec::new(),
            prev_depth: 0.0,
            shallow: false,
            last_fire: None,
            config,
        }
    }

    /// Consume the diagnostics of the most recent NoteOn, if any.
    pub fn take_fire_stats(&mut self) -> Option<FireStats> {
        self.last_fire.take()
    }

    /// Record fire diagnostics from the current sample buffer.
    fn record_fire(&mut self, velocity: u8, window_fired: bool) {
        let (n, first, last, span_ms) = match (self.samples.first(), self.samples.last()) {
            (Some(&(t0, d0)), Some(&(t1, d1))) => (
                self.samples.len(),
                d0,
                d1,
                t1.duration_since(t0).as_secs_f32() * 1000.0,
            ),
            _ => (0, 0.0, 0.0, 0.0),
        };
        self.last_fire = Some(FireStats {
            velocity,
            n_samples: n,
            first_depth: first,
            last_depth: last,
            span_ms,
            window_fired,
        });
    }

    /// Reset all rise/note state. `prev_depth` keeps the current depth so a
    /// key drifting *down* after a NoteOff can't read as upward movement.
    fn reset(&mut self, current_depth: f32) {
        self.state = FsmState::Idle;
        self.t_arm = None;
        self.t_window = None;
        self.samples.clear();
        self.shallow = false;
        self.prev_depth = current_depth;
    }

    /// Feed a fresh depth sample. `sensitivity` reshapes the curve by
    /// scaling the effective time-to-trigger (lower sens = faster press
    /// required to reach the same velocity). Full 1..=127 range is
    /// reachable at any positive sensitivity if the press is fast enough.
    pub fn update(&mut self, depth: f32, now: Instant, sensitivity: f32) -> VelocityEvent {
        let cfg = self.config;

        // True release (at/under the NKRO onset region, or the explicit zero
        // sample fed on NKRO key-up): full reset from any state.
        if depth < RELEASE_FLOOR {
            let was_active = matches!(self.state, FsmState::On | FsmState::Releasing);
            self.reset(depth);
            return if was_active {
                VelocityEvent::NoteOff
            } else {
                VelocityEvent::None
            };
        }

        // ── Sounding note: release tracking ─────────────────────────────────
        if matches!(self.state, FsmState::On | FsmState::Releasing) {
            self.prev_depth = depth;
            if self.shallow && depth >= cfg.off {
                self.shallow = false; // went deep enough for normal semantics
            }
            if !self.shallow && depth < cfg.off {
                self.reset(depth);
                return VelocityEvent::NoteOff;
            }
            match self.state {
                FsmState::On if depth < cfg.release => self.state = FsmState::Releasing,
                // No retrigger from a partial release; if depth recovers above
                // T_release, slip back to On silently.
                FsmState::Releasing if depth >= cfg.release => self.state = FsmState::On,
                _ => {}
            }
            return VelocityEvent::None;
        }

        // ── Pre-note: detection window + sampling ───────────────────────────
        let window_enabled = cfg.max_detect_ms > 0.0;
        let moving_down = depth > self.prev_depth + MOVE_EPS;
        if self.t_window.is_none() && moving_down {
            // A fresh rise begins. (Window opening requires upward movement so
            // the slow crawl of a releasing key can't ghost-open one.)
            self.t_window = Some(now);
            self.samples.clear();
        }
        self.prev_depth = depth;
        if self.t_window.is_some() {
            if self.samples.len() >= MAX_SAMPLES {
                self.samples.remove(0);
            }
            self.samples.push((now, depth));
        }

        // Arm bookkeeping (endpoint fallback for the trigger path).
        if self.state == FsmState::Idle && depth >= cfg.arm {
            self.t_arm = Some(now);
            self.state = FsmState::Armed;
        }

        // Fast strike: trigger crossed before the window elapsed — fire now.
        if depth >= cfg.trigger {
            let dt_ms = self
                .t_arm
                .map(|t| now.duration_since(t).as_secs_f32() * 1000.0)
                .unwrap_or(0.0);
            self.state = FsmState::On;
            self.shallow = false;
            let v = self.rise_velocity(dt_ms, sensitivity);
            self.record_fire(v, false);
            return VelocityEvent::NoteOn(v);
        }

        // Window elapsed: fire with what the rise earned — but never with
        // fewer than 2 samples (two points are the minimum that define a
        // speed; the window extends until the second sample arrives).
        if window_enabled {
            if let Some(t0) = self.t_window {
                let elapsed_ms = now.duration_since(t0).as_secs_f32() * 1000.0;
                if elapsed_ms >= cfg.max_detect_ms && self.samples.len() >= 2 {
                    if let Some(slope) = self.window_slope() {
                        self.state = FsmState::On;
                        self.shallow = depth < cfg.off;
                        let dt_equiv = (cfg.trigger - cfg.arm) / slope;
                        let v = self.compute_velocity(dt_equiv, sensitivity);
                        self.record_fire(v, true);
                        return VelocityEvent::NoteOn(v);
                    }
                    // Not actually pressing (resting finger / retreat): cancel
                    // and wait for the next real movement to reopen.
                    self.t_window = None;
                    self.samples.clear();
                }
            }
        }

        VelocityEvent::None
    }

    /// Least-squares slope (depth/ms) over the buffered rise, or None when
    /// degenerate or slower than a deliberate press.
    fn window_slope(&self) -> Option<f32> {
        let newest = self.samples.last()?.0;
        let mut n = 0.0f32;
        let (mut sx, mut sy, mut sxx, mut sxy) = (0.0f32, 0.0f32, 0.0f32, 0.0f32);
        for &(t, d) in &self.samples {
            let x = -(newest.duration_since(t).as_secs_f32() * 1000.0);
            n += 1.0;
            sx += x;
            sy += d;
            sxx += x * x;
            sxy += x * d;
        }
        if n < 2.0 {
            return None;
        }
        let denom = n * sxx - sx * sx;
        if denom <= f32::EPSILON {
            return None;
        }
        let slope = (n * sxy - sx * sy) / denom;
        (slope > MIN_SLOPE).then_some(slope)
    }

    pub fn is_active(&self) -> bool {
        matches!(self.state, FsmState::On | FsmState::Releasing)
    }

    pub fn force_off(&mut self) {
        self.reset(0.0);
    }

    /// Velocity at the trigger crossing: least-squares slope over the rise
    /// samples, expressed as the equivalent arm→trigger dt so the existing
    /// log-linear dt→velocity mapping (and the sensitivity semantics) are
    /// unchanged. Falls back to `endpoint_dt_ms` when the fit is degenerate.
    fn rise_velocity(&self, endpoint_dt_ms: f32, sensitivity: f32) -> u8 {
        match self.window_slope() {
            Some(slope) => {
                let dt_equiv = (self.config.trigger - self.config.arm) / slope;
                self.compute_velocity(dt_equiv, sensitivity)
            }
            None => self.compute_velocity(endpoint_dt_ms, sensitivity),
        }
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
    fn window_fires_at_deadline_on_partial_press() {
        // Press to 0.30 (below the 0.5 trigger) and hold: the note must fire
        // ~max_detect_ms after onset with the velocity the rise earned, and
        // holding at 0.30 (>= off) must NOT release it.
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        let mut fired_at_ms = None;
        for i in 0..30u64 {
            let d = (0.03 * i as f32).min(0.30); // reaches 0.30 at 10 ms, then holds
            let ev = fsm.update(d, t0 + Duration::from_millis(i), 1.0);
            match ev {
                VelocityEvent::NoteOn(v) => {
                    assert!(v >= 1, "window fire must carry a velocity");
                    fired_at_ms = Some(i);
                    break;
                }
                VelocityEvent::NoteOff => panic!("premature NoteOff"),
                VelocityEvent::None => {}
            }
        }
        let at = fired_at_ms.expect("partial press must fire at the window deadline");
        assert!((10..=12).contains(&at), "expected fire ~10ms after onset, got {at}ms");
        // Still held at 0.30: no NoteOff.
        let ev = fsm.update(0.30, t0 + Duration::from_millis(40), 1.0);
        assert_eq!(ev, VelocityEvent::None);
        assert!(fsm.is_active());
    }

    #[test]
    fn shallow_window_fire_releases_at_floor_not_off() {
        // A very light press that never exceeds T_off still fires at the
        // deadline — and must not be NoteOff'd by the off threshold while the
        // finger keeps resting there. Only the release floor (NKRO-up) ends it.
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        let mut fired = false;
        for i in 0..30u64 {
            let d = (0.015 * i as f32).min(0.06); // tops out below off (0.10)
            let ev = fsm.update(d, t0 + Duration::from_millis(i), 1.0);
            if matches!(ev, VelocityEvent::NoteOn(_)) {
                fired = true;
                break;
            }
        }
        assert!(fired, "shallow press must fire at the deadline");
        // Resting at 0.06 (< off) must not end the shallow note...
        for i in 30..40u64 {
            let ev = fsm.update(0.06, t0 + Duration::from_millis(i), 1.0);
            assert_eq!(ev, VelocityEvent::None, "shallow note cut by off threshold");
        }
        // ...but the true release (NKRO-up zero sample) must.
        let ev = fsm.update(0.0, t0 + Duration::from_millis(45), 1.0);
        assert_eq!(ev, VelocityEvent::NoteOff);
    }

    #[test]
    fn releasing_crawl_never_ghost_fires() {
        // Full press cycle, then the key crawls down slowly from just below
        // off to the floor. Downward motion must never open a window.
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        fsm.update(0.20, t0, 1.0);
        assert!(matches!(
            fsm.update(0.55, t0 + Duration::from_millis(2), 1.0),
            VelocityEvent::NoteOn(_)
        ));
        assert_eq!(
            fsm.update(0.09, t0 + Duration::from_millis(50), 1.0),
            VelocityEvent::NoteOff
        );
        // Slow downward crawl through the pre-note region for 60 ms.
        for (k, d) in [0.08, 0.07, 0.06, 0.05, 0.04, 0.03].iter().enumerate() {
            let ev = fsm.update(*d, t0 + Duration::from_millis(60 + 10 * k as u64), 1.0);
            assert_eq!(ev, VelocityEvent::None, "ghost fire during release crawl");
        }
    }

    #[test]
    fn resting_finger_cancels_window() {
        // A single tiny dip then stillness: at the deadline the slope is ~0,
        // so the window cancels and no note ever fires.
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let t0 = Instant::now();
        for i in 0..40u64 {
            let ev = fsm.update(0.05, t0 + Duration::from_millis(i), 1.0);
            assert_eq!(ev, VelocityEvent::None, "resting finger produced a note");
        }
    }

    #[test]
    fn window_disabled_waits_for_trigger() {
        // max_detect_ms = 0 (the Wooting default): a partial press must play
        // nothing, exactly as before the window existed.
        let cfg = VelocityConfig { max_detect_ms: 0.0, ..VelocityConfig::default() };
        let mut fsm = VelocityFsm::new(cfg);
        let t0 = Instant::now();
        for i in 0..60u64 {
            let d = (0.03 * i as f32).min(0.30);
            let ev = fsm.update(d, t0 + Duration::from_millis(i), 1.0);
            assert_eq!(ev, VelocityEvent::None, "window fired despite being disabled");
        }
    }

    #[test]
    fn idle_below_arm_does_nothing() {
        let mut fsm = VelocityFsm::new(VelocityConfig::default());
        let _ = at(0);
        assert_eq!(fsm.update(0.05, Instant::now(), 1.0), VelocityEvent::None);
        assert_eq!(fsm.update(0.10, Instant::now(), 1.0), VelocityEvent::None);
    }
}
