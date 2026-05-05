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
mod midi;
mod profiles;
mod rgb;
mod velocity;

use analog::AnalogSdk;
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
    analog: AnalogSdk,
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
    ///   analog_sdk_path: absolute path to `libwooting_analog_sdk.dylib`
    ///   rgb_sdk_path:    absolute path to `libwooting-rgb-sdk.dylib` (or None)
    ///   keycode_map:     dict[int, tuple[int,int,int]]   HID code -> (x, y, controller_note)
    ///   rgb_address_map: dict[tuple[int,int], tuple[int,int]]  (x,y) -> (row, col) for RGB
    ///   expected_product_ids: list[int]
    ///   config:          dict with optional knobs (thresholds, intervals, default profile)
    #[new]
    #[pyo3(signature = (analog_sdk_path, rgb_sdk_path, keycode_map, rgb_address_map, expected_product_ids, config))]
    fn new(
        analog_sdk_path: String,
        rgb_sdk_path: Option<String>,
        keycode_map: &Bound<'_, PyDict>,
        rgb_address_map: &Bound<'_, PyDict>,
        expected_product_ids: Vec<u16>,
        config: &Bound<'_, PyDict>,
    ) -> PyResult<Self> {
        let analog = AnalogSdk::open(&PathBuf::from(&analog_sdk_path))
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to load Analog SDK at {analog_sdk_path}: {e}")))?;

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
        let profile_config = ProfileConfig {
            velocity,
            channel_low: cfg_get_u32(config, "mpe_channel_low", 1)? as u8,
            channel_high: cfg_get_u32(config, "mpe_channel_high", 15)? as u8,
            aftertouch_enabled: cfg_get_bool(config, "aftertouch_enabled", true)?,
            aftertouch_smooth_alpha: cfg_get_f32(config, "aftertouch_smooth_alpha", 0.30)?,
            aftertouch_min_interval_ms: cfg_get_f32(config, "aftertouch_min_interval_ms", 5.0)?,
        };
        let default_profile = cfg_get_str(config, "default_profile", "mpe")?;
        let poll_us = cfg_get_u32(config, "min_poll_interval_us", 125)? as u64;
        let rgb_hz = cfg_get_f32(config, "rgb_refresh_hz", 30.0)?;

        let profile = profiles::build(&default_profile, profile_config.clone()).ok_or_else(|| {
            PyValueError::new_err(format!("Unknown default_profile: {default_profile}"))
        })?;

        let inner = Arc::new(BridgeInner {
            analog,
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
        // Initialize the SDK on the calling thread (synchronous and may briefly block).
        py.allow_threads(|| {
            if let Err(rc) = self.inner.analog.initialise() {
                self.inner
                    .last_error
                    .lock()
                    .replace(format!("Analog SDK initialise failed: {rc}"));
            }
            let _ = self.inner.analog.set_keycode_mode_hid();
            let connected = !self.inner.analog.get_connected_devices().is_empty();
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
            self.inner.analog.uninitialise();
            if let Some(rgb) = &self.inner.rgb {
                rgb.reset();
                rgb.close();
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
        let devices = self.inner.analog.get_connected_devices();
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

fn poll_thread_loop(inner: Arc<BridgeInner>) {
    let mut keycodes: Vec<u16> = vec![0; 128];
    let mut depths: Vec<f32> = vec![0.0; 128];
    let mut last_seen: HashMap<u16, f32> = HashMap::new();

    while inner.running.load(Ordering::Relaxed) {
        let next_deadline = Instant::now() + inner.poll_interval;

        let n = match inner.analog.read_full_buffer(&mut keycodes, &mut depths) {
            Ok(n) => n,
            Err(_) => 0,
        };
        let now = Instant::now();
        let mut seen_this_tick: HashMap<u16, f32> = HashMap::new();

        for i in 0..n {
            let kc = keycodes[i];
            let depth = depths[i];
            seen_this_tick.insert(kc, depth);
            last_seen.insert(kc, depth);
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
