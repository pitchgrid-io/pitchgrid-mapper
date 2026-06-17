//! `pg_wooting_bridge` — native Rust bridge between Wooting analog keyboards
//! and pitchgrid-mapper. Embedded into the Python app via PyO3 + maturin.
//!
//! All polling and per-key state machines run in native threads with no GIL
//! contention. Python drains a lock-free queue of MIDI bytes at a relaxed
//! cadence and pushes those bytes into `MIDIHandler.inject_message(...)`.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crossbeam_queue::SegQueue;
use parking_lot::Mutex;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

mod analog;
mod channels;
mod keymap;
mod madlions;
mod midi;
mod profiles;
mod rgb;
mod velocity;

use analog::AnalogSdk;
use madlions::{MadlionsDevice, KEYS_PER_POLL, TRAVEL_FULL};
use keymap::Keymap;
use profiles::{InputProfile, KeyDescriptor, ProfileConfig};
use rgb::RgbSdk;
use velocity::VelocityConfig;

#[derive(Default)]
struct PadColors {
    base: HashMap<(i16, i16), (u8, u8, u8)>,
    overlay: HashMap<(i16, i16), (u8, u8, u8)>,
    dirty: bool,
}

struct BridgeInner {
    // Mutex because the Rust Analog SDK API takes &mut self; the poll
    // thread, status queries, and lifecycle calls all touch it via
    // Arc<BridgeInner>.
    analog: Mutex<AnalogSdk>,
    /// Plugin directory passed to `analog.initialise(...)` at start().
    plugin_dir: std::path::PathBuf,
    /// Present iff this bridge drives a Madlions-family board: analog is read
    /// by polling `0xFF60` (not the Analog SDK) and RGB rides the same handle.
    madlions: Option<Mutex<MadlionsDevice>>,
    rgb: Option<RgbSdk>,
    keymap: Keymap,
    expected_product_ids: Vec<u16>,
    profile_config: ProfileConfig,
    profile: Mutex<Box<dyn InputProfile>>,
    midi_outbox: Arc<SegQueue<Vec<u8>>>,
    pad_colors: Mutex<PadColors>,
    poll_interval: Duration,
    rgb_refresh_interval: Duration,
    running: AtomicBool,
    connected: AtomicBool,
    last_error: Mutex<Option<String>>,
}

#[pyclass]
struct Bridge {
    inner: Arc<BridgeInner>,
    threads: Mutex<Vec<std::thread::JoinHandle<()>>>,
}

fn cfg_get_f32(d: &Bound<'_, PyDict>, key: &str, default: f32) -> PyResult<f32> {
    if let Some(v) = d.get_item(key)? {
        Ok(v.extract::<f32>()?)
    } else {
        Ok(default)
    }
}

fn cfg_get_u32(d: &Bound<'_, PyDict>, key: &str, default: u32) -> PyResult<u32> {
    if let Some(v) = d.get_item(key)? {
        Ok(v.extract::<u32>()?)
    } else {
        Ok(default)
    }
}

fn cfg_get_bool(d: &Bound<'_, PyDict>, key: &str, default: bool) -> PyResult<bool> {
    if let Some(v) = d.get_item(key)? {
        Ok(v.extract::<bool>()?)
    } else {
        Ok(default)
    }
}

fn cfg_get_str(d: &Bound<'_, PyDict>, key: &str, default: &str) -> PyResult<String> {
    if let Some(v) = d.get_item(key)? {
        Ok(v.extract::<String>()?)
    } else {
        Ok(default.to_string())
    }
}

#[pymethods]
impl Bridge {
    /// Construct a bridge.
    ///
    /// Args:
    ///   analog_plugin_dir: directory containing the Wooting Analog SDK
    ///       plugin(s). The SDK itself is statically linked into this
    ///       binary — no separate dylib is loaded for the SDK; only the
    ///       plugin(s) are dlopen'd. Typical values:
    ///         - dev:  /usr/local/share/WootingAnalogPlugins
    ///         - app:  <Bundle>.app/Contents/Resources/WootingAnalogPlugins
    ///   rgb_sdk_path:      absolute path to `libwooting-rgb-sdk.dylib` (or None).
    ///   keycode_map:       dict[int, tuple[int,int,int]] HID -> (x, y, note).
    ///   rgb_address_map:   dict[tuple[int,int], tuple[int,int]] (x,y) -> (row, col).
    ///   expected_product_ids: list[int].
    ///   config:            dict with optional knobs (thresholds, intervals, default profile).
    #[new]
    #[pyo3(signature = (analog_plugin_dir, rgb_sdk_path, keycode_map, rgb_address_map, expected_product_ids, config, madlions_product_id=None, madlions_index_keycode=None, madlions_slot_map=None))]
    fn new(
        analog_plugin_dir: String,
        rgb_sdk_path: Option<String>,
        keycode_map: &Bound<'_, PyDict>,
        rgb_address_map: &Bound<'_, PyDict>,
        expected_product_ids: Vec<u16>,
        config: &Bound<'_, PyDict>,
        madlions_product_id: Option<u16>,
        madlions_index_keycode: Option<&Bound<'_, PyDict>>,
        madlions_slot_map: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let analog = AnalogSdk::new();

        // Madlions-family board: open the 0xFF60 interface for analog polling
        // (and RGB). When present, the Analog SDK path is bypassed entirely.
        let madlions = if let Some(pid) = madlions_product_id {
            let mut idx_kc: HashMap<u16, u16> = HashMap::new();
            if let Some(d) = madlions_index_keycode {
                for (k, v) in d.iter() {
                    idx_kc.insert(k.extract::<u16>()?, v.extract::<u16>()?);
                }
            }
            let mut xy_slot: HashMap<(i16, i16), u16> = HashMap::new();
            if let Some(d) = madlions_slot_map {
                for (k, v) in d.iter() {
                    let key: (i16, i16) = k.extract()?;
                    xy_slot.insert(key, v.extract::<u16>()?);
                }
            }
            match MadlionsDevice::open(pid, idx_kc, xy_slot) {
                Ok(dev) => Some(Mutex::new(dev)),
                Err(e) => return Err(PyRuntimeError::new_err(format!("Madlions open failed: {e}"))),
            }
        } else {
            None
        };

        let rgb = match rgb_sdk_path {
            Some(p) => match RgbSdk::open(&PathBuf::from(&p)) {
                Ok(r) => Some(r),
                Err(e) => {
                    log::warn!("RGB SDK failed to load: {e}");
                    None
                }
            },
            None => None,
        };

        // Build keymap. Logical coords are signed because some controllers use
        // negative x for offset rows (e.g. 60HE's RowOffsets).
        let mut km = Keymap::default();
        for (k, v) in keycode_map.iter() {
            let kc: u16 = k.extract()?;
            let tup: (i16, i16, u8) = v.extract()?;
            km.map.insert(
                kc,
                KeyDescriptor {
                    logical_x: tup.0,
                    logical_y: tup.1,
                    controller_note: tup.2,
                },
            );
        }
        for (k, v) in rgb_address_map.iter() {
            let logical: (i16, i16) = k.extract()?;
            let hw: (u8, u8) = v.extract()?;
            km.rgb_addr.insert(logical, hw);
        }

        // Profile config.
        let velocity = VelocityConfig {
            arm: cfg_get_f32(config, "velocity_arm_threshold", 0.15)?,
            trigger: cfg_get_f32(config, "velocity_trigger_threshold", 0.50)?,
            release: cfg_get_f32(config, "velocity_release_threshold", 0.30)?,
            off: cfg_get_f32(config, "velocity_off_threshold", 0.10)?,
            min_dt_ms: cfg_get_f32(config, "velocity_min_dt_ms", 2.0)?,
            max_dt_ms: cfg_get_f32(config, "velocity_max_dt_ms", 80.0)?,
        };
        // Sustain state shared between the bridge poll thread and any
        // active profile. master_sustain follows the spacebar pedal;
        // per_note_sustain_enabled is the runtime switch (default off).
        let master_sustain = Arc::new(AtomicBool::new(false));
        let per_note_sustain_enabled = Arc::new(AtomicBool::new(
            cfg_get_bool(config, "per_note_sustain_enabled", false)?,
        ));
        let initial_sens = cfg_get_f32(config, "sensitivity", 1.0)?;
        let sensitivity = Arc::new(std::sync::atomic::AtomicU32::new(
            initial_sens.clamp(0.0, 8.0).to_bits(),
        ));

        let profile_config = ProfileConfig {
            velocity,
            channel_low: cfg_get_u32(config, "mpe_channel_low", 1)? as u8,
            channel_high: cfg_get_u32(config, "mpe_channel_high", 15)? as u8,
            aftertouch_enabled: cfg_get_bool(config, "aftertouch_enabled", true)?,
            aftertouch_smooth_alpha: cfg_get_f32(config, "aftertouch_smooth_alpha", 0.30)?,
            aftertouch_min_interval_ms: cfg_get_f32(config, "aftertouch_min_interval_ms", 5.0)?,
            master_sustain: master_sustain.clone(),
            per_note_sustain_enabled: per_note_sustain_enabled.clone(),
            sensitivity: sensitivity.clone(),
        };
        let default_profile = cfg_get_str(config, "default_profile", "mpe")?;
        let poll_us = cfg_get_u32(config, "min_poll_interval_us", 125)? as u64;
        let rgb_hz = cfg_get_f32(config, "rgb_refresh_hz", 30.0)?;

        let profile = profiles::build(&default_profile, profile_config.clone()).ok_or_else(|| {
            PyValueError::new_err(format!("Unknown default_profile: {default_profile}"))
        })?;

        let inner = Arc::new(BridgeInner {
            analog: Mutex::new(analog),
            plugin_dir: PathBuf::from(&analog_plugin_dir),
            madlions,
            rgb,
            keymap: km,
            expected_product_ids,
            profile_config,
            profile: Mutex::new(profile),
            midi_outbox: Arc::new(SegQueue::new()),
            pad_colors: Mutex::new(PadColors::default()),
            poll_interval: Duration::from_micros(poll_us),
            rgb_refresh_interval: Duration::from_secs_f32(1.0 / rgb_hz.max(1.0)),
            running: AtomicBool::new(false),
            connected: AtomicBool::new(false),
            last_error: Mutex::new(None),
        });

        Ok(Self {
            inner,
            threads: Mutex::new(Vec::new()),
        })
    }

    fn start(&self, py: Python<'_>) -> PyResult<()> {
        if self.inner.running.swap(true, Ordering::SeqCst) {
            return Ok(()); // already running
        }

        // Madlions path: no Analog SDK init, no RGB thread. One thread owns the
        // 0xFF60 handle and does analog polling (plus RGB flushing, later).
        if self.inner.madlions.is_some() {
            py.allow_threads(|| {
                self.inner.connected.store(true, Ordering::SeqCst);
                let mut prof = self.inner.profile.lock();
                for msg in prof.priming() {
                    self.inner.midi_outbox.push(msg.to_vec());
                }
            });
            let inner = Arc::clone(&self.inner);
            let t = std::thread::Builder::new()
                .name("madlions-poll".into())
                .spawn(move || madlions_poll_thread_loop(inner))
                .map_err(|e| PyRuntimeError::new_err(format!("spawn madlions thread: {e}")))?;
            self.threads.lock().push(t);
            return Ok(());
        }

        // Initialize the SDK on the calling thread (synchronous and may briefly block).
        py.allow_threads(|| {
            let mut analog = self.inner.analog.lock();
            if let Err(rc) = analog.initialise(&self.inner.plugin_dir) {
                self.inner
                    .last_error
                    .lock()
                    .replace(format!("Analog SDK initialise failed: {rc}"));
            }
            let _ = analog.set_keycode_mode_hid();
            let connected = !analog.get_connected_devices().is_empty();
            self.inner.connected.store(connected, Ordering::SeqCst);
            if let Some(rgb) = &self.inner.rgb {
                rgb.set_auto_update(false);
                let _ = rgb.connected();
            }

            // Send profile priming MIDI into the outbox.
            let mut prof = self.inner.profile.lock();
            for msg in prof.priming() {
                self.inner.midi_outbox.push(msg.to_vec());
            }
        });

        let poll_inner = Arc::clone(&self.inner);
        let poll_thread = std::thread::Builder::new()
            .name("wooting-poll".into())
            .spawn(move || poll_thread_loop(poll_inner))
            .map_err(|e| PyRuntimeError::new_err(format!("spawn poll thread: {e}")))?;

        let rgb_inner = Arc::clone(&self.inner);
        let rgb_thread = std::thread::Builder::new()
            .name("wooting-rgb".into())
            .spawn(move || rgb_thread_loop(rgb_inner))
            .map_err(|e| PyRuntimeError::new_err(format!("spawn rgb thread: {e}")))?;

        let mut handles = self.threads.lock();
        handles.push(poll_thread);
        handles.push(rgb_thread);
        Ok(())
    }

    fn stop(&self, py: Python<'_>) -> PyResult<()> {
        if !self.inner.running.swap(false, Ordering::SeqCst) {
            return Ok(());
        }
        // Drain shutdown messages from the active profile.
        {
            let mut prof = self.inner.profile.lock();
            for msg in prof.shutdown() {
                self.inner.midi_outbox.push(msg.to_vec());
            }
        }
        let mut handles = self.threads.lock();
        let drained: Vec<_> = handles.drain(..).collect();
        drop(handles);
        py.allow_threads(|| {
            for h in drained {
                let _ = h.join();
            }
            // Only the Wooting path initialises the Analog SDK / RGB SDK.
            if self.inner.madlions.is_none() {
                self.inner.analog.lock().uninitialise();
                if let Some(rgb) = &self.inner.rgb {
                    rgb.reset();
                    rgb.close();
                }
            }
        });
        Ok(())
    }

    fn set_profile(&self, name: &str) -> PyResult<()> {
        let new_profile = profiles::build(name, self.inner.profile_config.clone())
            .ok_or_else(|| PyValueError::new_err(format!("Unknown profile: {name}")))?;
        let mut prof = self.inner.profile.lock();
        for msg in prof.shutdown() {
            self.inner.midi_outbox.push(msg.to_vec());
        }
        *prof = new_profile;
        for msg in prof.priming() {
            self.inner.midi_outbox.push(msg.to_vec());
        }
        Ok(())
    }

    fn active_profile(&self) -> String {
        self.inner.profile.lock().meta().name.to_string()
    }

    /// Toggle the experimental per-note CC64 sustain. When enabled, the
    /// piano-sim profile (and any future profile that opts in) emits
    /// CC64=127 on the note's member channel at strike. The matching
    /// CC64=0 on release is suppressed while the master pedal (spacebar)
    /// is asserting sustain, so per-note state never fights the pedal.
    fn set_per_note_sustain(&self, enabled: bool) {
        self.inner
            .profile_config
            .per_note_sustain_enabled
            .store(enabled, Ordering::Release);
    }

    fn per_note_sustain_enabled(&self) -> bool {
        self.inner
            .profile_config
            .per_note_sustain_enabled
            .load(Ordering::Acquire)
    }

    /// Set the analog-input sensitivity multiplier (1.0 = neutral, >1.0
    /// louder, <1.0 quieter). Applies to all profiles. Clamped 0..=8.
    fn set_sensitivity(&self, value: f32) {
        profiles::store_sensitivity(&self.inner.profile_config.sensitivity, value);
    }

    fn sensitivity(&self) -> f32 {
        profiles::read_sensitivity(&self.inner.profile_config.sensitivity)
    }

    fn set_pad_colors(&self, list: &Bound<'_, PyList>) -> PyResult<()> {
        let mut pc = self.inner.pad_colors.lock();
        pc.base.clear();
        for item in list.iter() {
            let (x, y, r, g, b): (i16, i16, u8, u8, u8) = item.extract()?;
            pc.base.insert((x, y), (r, g, b));
        }
        pc.dirty = true;
        Ok(())
    }

    fn set_pad_overlay(&self, x: i16, y: i16, r: u8, g: u8, b: u8) {
        let mut pc = self.inner.pad_colors.lock();
        pc.overlay.insert((x, y), (r, g, b));
        pc.dirty = true;
    }

    fn clear_pad_overlay(&self, x: i16, y: i16) {
        let mut pc = self.inner.pad_colors.lock();
        pc.overlay.remove(&(x, y));
        pc.dirty = true;
    }

    /// Drain all pending outgoing MIDI messages. Each item is a bytes object
    /// representing one complete MIDI message ready for `MIDIHandler.inject_message`.
    fn drain_midi<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let list = PyList::empty_bound(py);
        while let Some(msg) = self.inner.midi_outbox.pop() {
            list.append(PyBytes::new_bound(py, &msg))?;
        }
        Ok(list)
    }

    fn status<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new_bound(py);
        d.set_item("running", self.inner.running.load(Ordering::SeqCst))?;
        d.set_item("connected", self.inner.connected.load(Ordering::SeqCst))?;
        let devices = self.inner.analog.lock().get_connected_devices();
        let dev_list = PyList::empty_bound(py);
        for d_ in devices {
            let entry = PyDict::new_bound(py);
            entry.set_item("vendor_id", d_.vendor_id)?;
            entry.set_item("product_id", d_.product_id)?;
            entry.set_item("device_name", d_.device_name)?;
            entry.set_item("manufacturer_name", d_.manufacturer_name)?;
            entry.set_item("device_id", d_.device_id)?;
            dev_list.append(entry)?;
        }
        d.set_item("devices", dev_list)?;
        d.set_item(
            "last_error",
            self.inner.last_error.lock().clone().unwrap_or_default(),
        )?;
        d.set_item(
            "expected_product_ids",
            self.inner.expected_product_ids.clone(),
        )?;
        Ok(d)
    }
}

/// HID Keyboard Usage code for spacebar — repurposed as a sustain pedal.
const SPACEBAR_HID: u16 = 0x2C;
/// Hysteresis thresholds for spacebar-as-sustain. Down at >0.5, lift at <0.3,
/// matching the rough feel of a half-pedal release on a real damper pedal.
const SUSTAIN_DOWN_THRESHOLD: f32 = 0.50;
const SUSTAIN_UP_THRESHOLD: f32 = 0.30;
/// Sustain CC and target channel. MPE-friendly: master/manager channel is
/// channel 1 (0-indexed 0), so most synths receive the pedal globally.
const CC_SUSTAIN: u8 = 0x40;
const SUSTAIN_CHANNEL: u8 = 0;

fn push_sustain(inner: &Arc<BridgeInner>, on: bool) {
    inner
        .profile_config
        .master_sustain
        .store(on, Ordering::Release);
    let val: u8 = if on { 127 } else { 0 };
    inner
        .midi_outbox
        .push(vec![0xB0 | (SUSTAIN_CHANNEL & 0x0F), CC_SUSTAIN, val]);
}

fn poll_thread_loop(inner: Arc<BridgeInner>) {
    let mut keycodes: Vec<u16> = vec![0; 128];
    let mut depths: Vec<f32> = vec![0.0; 128];
    let mut last_seen: HashMap<u16, f32> = HashMap::new();
    let mut sustain_down = false;

    while inner.running.load(Ordering::Relaxed) {
        let next_deadline = Instant::now() + inner.poll_interval;

        let n = {
            let mut analog = inner.analog.lock();
            match analog.read_full_buffer(&mut keycodes, &mut depths) {
                Ok(n) => n,
                Err(_) => 0,
            }
        };
        let now = Instant::now();
        let mut seen_this_tick: HashMap<u16, f32> = HashMap::new();
        let mut spacebar_seen_this_tick = false;

        for i in 0..n {
            let kc = keycodes[i];
            let depth = depths[i];
            seen_this_tick.insert(kc, depth);
            last_seen.insert(kc, depth);

            if kc == SPACEBAR_HID {
                spacebar_seen_this_tick = true;
                let new_down = if sustain_down {
                    depth >= SUSTAIN_UP_THRESHOLD
                } else {
                    depth >= SUSTAIN_DOWN_THRESHOLD
                };
                if new_down != sustain_down {
                    sustain_down = new_down;
                    push_sustain(&inner, sustain_down);
                }
                continue;
            }

            if let Some(key) = inner.keymap.lookup(kc) {
                let mut prof = inner.profile.lock();
                let batch = prof.process(key, kc, depth, now);
                drop(prof);
                for msg in batch {
                    inner.midi_outbox.push(msg.to_vec());
                }
            }
        }

        // Synthesize zero-depth samples for keys we previously saw but no longer
        // appear in the buffer (the SDK omits fully-released keys). This drives
        // the FSM to emit NoteOff for keys that were lifted between polls.
        let stale: Vec<u16> = last_seen
            .keys()
            .filter(|k| !seen_this_tick.contains_key(k))
            .copied()
            .collect();
        for kc in stale {
            last_seen.insert(kc, 0.0);
            if kc == SPACEBAR_HID {
                if sustain_down && !spacebar_seen_this_tick {
                    sustain_down = false;
                    push_sustain(&inner, false);
                }
                continue;
            }
            if let Some(key) = inner.keymap.lookup(kc) {
                let mut prof = inner.profile.lock();
                let batch = prof.process(key, kc, 0.0, now);
                drop(prof);
                for msg in batch {
                    inner.midi_outbox.push(msg.to_vec());
                }
            }
        }
        // Drop entries that have settled at zero for a while.
        last_seen.retain(|_, v| *v > 0.001);

        let now = Instant::now();
        if now < next_deadline {
            spin_sleep::sleep(next_deadline - now);
        }
    }

    // Lifting the bridge: if we exited with sustain still asserted, release
    // it so downstream synths don't ring forever.
    if sustain_down {
        push_sustain(&inner, false);
    }
}

fn rgb_thread_loop(inner: Arc<BridgeInner>) {
    let Some(rgb) = inner.rgb.as_ref() else {
        return;
    };
    let mut last_push = Instant::now() - inner.rgb_refresh_interval;
    while inner.running.load(Ordering::Relaxed) {
        let now = Instant::now();
        let mut due = now.duration_since(last_push) >= inner.rgb_refresh_interval;
        {
            let pc = inner.pad_colors.lock();
            if pc.dirty {
                due = true;
            }
        }
        if due && rgb.connected() {
            let mut pc = inner.pad_colors.lock();
            for (logical, &(r, g, b)) in pc.base.iter() {
                let (r2, g2, b2) = pc.overlay.get(logical).copied().unwrap_or((r, g, b));
                if let Some((row, col)) = inner.keymap.rgb_for_logical(logical.0, logical.1) {
                    rgb.set_single(row, col, r2, g2, b2);
                }
            }
            pc.dirty = false;
            drop(pc);
            rgb.update_keyboard();
            last_push = Instant::now();
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// Madlions analog poll loop. Reads per-key travel over `0xFF60` and feeds the
/// active profile, exactly like `poll_thread_loop` does for Wooting — only the
/// source differs. Rising keys (in the velocity window) get exclusive priority
/// so fast strikes are sampled at ~kHz; otherwise we poll held keys plus one
/// rotating background chunk to catch new presses.
fn madlions_poll_thread_loop(inner: Arc<BridgeInner>) {
    let Some(mad) = inner.madlions.as_ref() else {
        return;
    };
    let (index_keycode, chunks, xy_to_slot) = {
        let m = mad.lock();
        (m.index_to_keycode.clone(), m.chunks.clone(), m.xy_to_slot.clone())
    };
    if chunks.is_empty() {
        return;
    }
    let mut last_rgb = Instant::now() - inner.rgb_refresh_interval;

    // Raw-travel thresholds (0..=350) mirroring the velocity FSM's normalised
    // arm/trigger/off so "rising" here means "between arm and trigger".
    const OFF_RAW: u16 = 35; // ~0.10
    const ARM_RAW: u16 = 45; // ~0.13
    const TRIG_RAW: u16 = 180; // ~0.51

    let mut last: HashMap<u16, u16> = HashMap::new(); // index -> travel

    // Idle/held polling throttle. Continuous max-rate polling saturates the
    // shared 0xFF60 handle and suppresses RGB display on macOS, so when no key
    // is mid-strike we pace the loop; a rising key keeps full rate.
    const IDLE_THROTTLE: Duration = Duration::from_micros(2500);

    let is_rising = |c: u8, last: &HashMap<u16, u16>| {
        (0..KEYS_PER_POLL).any(|j| {
            let idx = c as u16 + j;
            index_keycode.contains_key(&idx)
                && last.get(&idx).map_or(false, |&d| d > ARM_RAW && d < TRIG_RAW)
        })
    };

    while inner.running.load(Ordering::Relaxed) {
        let rising: Vec<u8> =
            chunks.iter().copied().filter(|&c| is_rising(c, &last)).collect();
        let busy = !rising.is_empty();

        let to_poll: Vec<u8> = if busy {
            // A key is mid-strike: poll only its chunk(s) at full rate so the
            // velocity FSM gets a dense rising edge.
            rising
        } else {
            // Idle/held: one full scan per cycle (all chunks) so new presses are
            // caught within a single sweep; the throttle below then frees the
            // handle for RGB.
            chunks.clone()
        };

        for off in to_poll {
            let travels = {
                let m = mad.lock();
                m.poll_chunk(off)
            };
            let Some(travels) = travels else { continue };
            let now = Instant::now();
            for j in 0..KEYS_PER_POLL {
                let idx = off as u16 + j;
                let Some(&keycode) = index_keycode.get(&idx) else {
                    continue;
                };
                let travel = travels[j as usize];
                last.insert(idx, travel);
                let depth = travel as f32 / TRAVEL_FULL;
                if let Some(key) = inner.keymap.lookup(keycode) {
                    let batch = {
                        let mut prof = inner.profile.lock();
                        prof.process(key, keycode, depth, now)
                    };
                    for msg in batch {
                        inner.midi_outbox.push(msg.to_vec());
                    }
                }
            }
        }

        // RGB flush — rate-limited to rgb_refresh_interval, coalesced via the
        // pad_colors dirty flag. Interleaving RGB writes with analog polls on
        // the shared handle is fine as long as polling is throttled (below).
        let due = {
            let pc = inner.pad_colors.lock();
            pc.dirty || Instant::now().duration_since(last_rgb) >= inner.rgb_refresh_interval
        };
        if due {
            let mut frame = [(0u8, 0u8, 0u8); madlions::NUM_RGB_SLOTS];
            {
                let mut pc = inner.pad_colors.lock();
                for (xy, &(r, g, b)) in pc.base.iter() {
                    let (r2, g2, b2) = pc.overlay.get(xy).copied().unwrap_or((r, g, b));
                    if let Some(&slot) = xy_to_slot.get(xy) {
                        if (slot as usize) < madlions::NUM_RGB_SLOTS {
                            frame[slot as usize] = (r2, g2, b2);
                        }
                    }
                }
                pc.dirty = false;
            }
            {
                let m = mad.lock();
                m.write_rgb(&frame);
            }
            last_rgb = Instant::now();
        }

        // Pace idle/held polling so it can't saturate the shared handle and
        // starve RGB; a rising key keeps full rate for accurate velocity.
        if !busy {
            std::thread::sleep(IDLE_THROTTLE);
        }
    }
}

#[pyfunction]
fn available_profiles<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
    let list = PyList::empty_bound(py);
    for meta in profiles::registry() {
        let d = PyDict::new_bound(py);
        d.set_item("name", meta.name)?;
        d.set_item("label", meta.label)?;
        d.set_item("description", meta.description)?;
        list.append(d)?;
    }
    Ok(list)
}

#[pymodule]
fn pg_wooting_bridge(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Bridge>()?;
    m.add_function(wrap_pyfunction!(available_profiles, m)?)?;
    Ok(())
}
