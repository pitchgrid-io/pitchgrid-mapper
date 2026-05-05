//! Per-key analog-travel recorder.
//!
//! Polls the Wooting Analog SDK at ~8 kHz, keeps a rolling 1000-sample
//! pre-trigger buffer per HID keycode, and on a press (depth crossing 0.5
//! upward from below 0.5) starts capturing 7000 post-trigger samples. Once
//! 7000 post-trigger samples have been written, dumps the full 8000-sample
//! window (1000 pre + 7000 post) to a CSV named
//! `key_<HID>_<UTC-timestamp>.csv` in the configured output directory.
//!
//! Designed for analyzing noise floors and switch travel curves, not for
//! production use. Standalone binary that dlopens the system-installed
//! Wooting Analog SDK dylib directly so it stays decoupled from the PyO3
//! extension build.
//!
//! Run with:
//!   cargo run --release --bin key_recorder -- --output-dir /tmp/wooting-traces
//!
//! Default output dir is the current directory.

use std::collections::{HashMap, HashSet};
use std::ffi::CStr;
use std::fs::{create_dir_all, File};
use std::io::{BufWriter, Write};
use std::os::raw::{c_char, c_float, c_int, c_uint, c_ushort};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use libloading::{Library, Symbol};

const SAMPLE_RATE_HZ: u32 = 8000;
const POLL_INTERVAL: Duration = Duration::from_micros(1_000_000 / SAMPLE_RATE_HZ as u64);
const PRE_SAMPLES: usize = 1000;
const POST_SAMPLES: usize = 7000;
const TOTAL_SAMPLES: usize = PRE_SAMPLES + POST_SAMPLES;
const TRIGGER_THRESHOLD: f32 = 0.5;
const READ_BUFFER_CAP: usize = 128;

/// Default Apple Silicon SDK path. Override via $WOOTING_ANALOG_SDK_PATH.
const DEFAULT_SDK_PATH: &str = "/usr/local/lib/libwooting_analog_sdk.dylib";

// --- minimal dlopen wrapper around the SDK -------------------------------

#[repr(C)]
#[derive(Clone, Copy)]
struct DeviceInfoFFI {
    vendor_id: u16,
    product_id: u16,
    manufacturer_name: *const c_char,
    device_name: *const c_char,
    device_id: u64,
    device_type: c_int,
}

struct AnalogSdk {
    _lib: Library,
    initialise: unsafe extern "C" fn() -> c_int,
    uninitialise: unsafe extern "C" fn() -> c_int,
    set_keycode_mode: unsafe extern "C" fn(c_uint) -> c_int,
    read_full_buffer: unsafe extern "C" fn(*mut c_ushort, *mut c_float, c_uint) -> c_int,
    get_devices: unsafe extern "C" fn(*mut *const DeviceInfoFFI, c_uint) -> c_int,
}

unsafe fn load_fn<T: Copy>(lib: &Library, name: &[u8]) -> Result<T, String> {
    let s: Symbol<T> = lib.get(name).map_err(|e| e.to_string())?;
    Ok(*s)
}

impl AnalogSdk {
    fn open(path: &Path) -> Result<Self, String> {
        let lib = unsafe { Library::new(path) }.map_err(|e| format!("dlopen: {e}"))?;
        let initialise = unsafe { load_fn(&lib, b"wooting_analog_initialise")? };
        let uninitialise = unsafe { load_fn(&lib, b"wooting_analog_uninitialise")? };
        let set_keycode_mode = unsafe { load_fn(&lib, b"wooting_analog_set_keycode_mode")? };
        let read_full_buffer = unsafe { load_fn(&lib, b"wooting_analog_read_full_buffer")? };
        let get_devices =
            unsafe { load_fn(&lib, b"wooting_analog_get_connected_devices_info")? };
        Ok(Self {
            _lib: lib,
            initialise,
            uninitialise,
            set_keycode_mode,
            read_full_buffer,
            get_devices,
        })
    }

    fn initialise(&self) -> Result<u32, i32> {
        let rc = unsafe { (self.initialise)() };
        if rc < 0 { Err(rc) } else { Ok(rc as u32) }
    }

    fn uninitialise(&self) {
        unsafe { (self.uninitialise)(); }
    }

    fn set_keycode_mode_hid(&self) -> Result<(), i32> {
        let rc = unsafe { (self.set_keycode_mode)(0) };
        if rc < 0 { Err(rc) } else { Ok(()) }
    }

    fn read_full_buffer(&self, codes: &mut [u16], depths: &mut [f32]) -> Result<usize, i32> {
        let cap = codes.len().min(depths.len()) as c_uint;
        let rc = unsafe {
            (self.read_full_buffer)(codes.as_mut_ptr(), depths.as_mut_ptr(), cap)
        };
        if rc < 0 { Err(rc) } else { Ok(rc as usize) }
    }

    fn list_devices(&self) -> Vec<(u16, u16, String)> {
        let mut buf: Vec<*const DeviceInfoFFI> = vec![std::ptr::null(); 16];
        let n = unsafe { (self.get_devices)(buf.as_mut_ptr(), buf.len() as c_uint) };
        let mut out = Vec::new();
        if n <= 0 {
            return out;
        }
        for i in 0..n as usize {
            let p = buf[i];
            if p.is_null() {
                continue;
            }
            unsafe {
                let info = *p;
                let name = if info.device_name.is_null() {
                    String::new()
                } else {
                    CStr::from_ptr(info.device_name).to_string_lossy().into_owned()
                };
                out.push((info.vendor_id, info.product_id, name));
            }
        }
        out
    }
}

// --- recorder state ------------------------------------------------------

#[derive(Clone, Copy, Default)]
struct Sample {
    /// Microseconds since recorder start.
    t_us: u64,
    depth: f32,
}

struct KeyRecorder {
    /// Ring buffer of the most recent TOTAL_SAMPLES.
    ring: Vec<Sample>,
    /// Index where the *next* sample will be written (wraps).
    head: usize,
    /// Total samples ever written.
    samples_written: u64,
    /// Last seen depth, for edge detection.
    last_depth: f32,
    /// Global sample index AT which the current trigger fired, if any.
    trigger_global_idx: Option<u64>,
}

impl KeyRecorder {
    fn new() -> Self {
        Self {
            ring: vec![Sample::default(); TOTAL_SAMPLES],
            head: 0,
            samples_written: 0,
            last_depth: 0.0,
            trigger_global_idx: None,
        }
    }

    /// Append a sample. Returns Some(snapshot) if a window is now ready to
    /// dump (POST_SAMPLES samples written since the trigger). Snapshot is
    /// in chronological order, oldest first; trigger sits at index PRE_SAMPLES.
    fn push(&mut self, sample: Sample) -> Option<Vec<Sample>> {
        let crossed =
            self.last_depth < TRIGGER_THRESHOLD && sample.depth >= TRIGGER_THRESHOLD;
        let was_recording = self.trigger_global_idx.is_some();

        self.ring[self.head] = sample;
        self.head = (self.head + 1) % TOTAL_SAMPLES;
        self.samples_written = self.samples_written.saturating_add(1);
        self.last_depth = sample.depth;

        if crossed && !was_recording {
            // Mark this just-written sample as the trigger.
            self.trigger_global_idx = Some(self.samples_written - 1);
            return None;
        }

        if let Some(trig_idx) = self.trigger_global_idx {
            // Number of samples written including the trigger sample itself.
            let inclusive = (self.samples_written - 1) - trig_idx + 1;
            if inclusive >= POST_SAMPLES as u64 {
                let snap = self.snapshot_in_order();
                self.trigger_global_idx = None;
                return Some(snap);
            }
        }
        None
    }

    fn snapshot_in_order(&self) -> Vec<Sample> {
        let mut out = Vec::with_capacity(TOTAL_SAMPLES);
        for i in 0..TOTAL_SAMPLES {
            out.push(self.ring[(self.head + i) % TOTAL_SAMPLES]);
        }
        out
    }
}

fn write_csv(
    output_dir: &Path,
    hid_code: u16,
    samples: &[Sample],
) -> std::io::Result<PathBuf> {
    create_dir_all(output_dir)?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let ts = format_iso_for_filename(now);
    let path = output_dir.join(format!("key_0x{:02X}_{}.csv", hid_code, ts));
    let f = File::create(&path)?;
    let mut w = BufWriter::new(f);
    writeln!(w, "sample_idx,t_us_relative_to_trigger,depth")?;
    let trigger_t_us = samples[PRE_SAMPLES].t_us as i64;
    for (i, s) in samples.iter().enumerate() {
        let rel = s.t_us as i64 - trigger_t_us;
        writeln!(w, "{},{},{:.6}", i, rel, s.depth)?;
    }
    w.flush()?;
    Ok(path)
}

fn format_iso_for_filename(d: Duration) -> String {
    let secs = d.as_secs() as i64;
    let millis = d.subsec_millis();
    let (y, mo, da, h, mi, s) = epoch_to_ymdhms(secs);
    format!(
        "{:04}{:02}{:02}T{:02}{:02}{:02}{:03}",
        y, mo, da, h, mi, s, millis
    )
}

fn epoch_to_ymdhms(secs: i64) -> (i32, u32, u32, u32, u32, u32) {
    // Gregorian (Howard Hinnant). Filename uniqueness is the only goal.
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400) as u32;
    let h = rem / 3600;
    let mi = (rem % 3600) / 60;
    let s = rem % 60;
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = y + if m <= 2 { 1 } else { 0 };
    (y as i32, m, d, h, mi, s)
}

fn label_for(hid: u16) -> &'static str {
    match hid {
        0x04 => "A", 0x05 => "B", 0x06 => "C", 0x07 => "D", 0x08 => "E",
        0x09 => "F", 0x0A => "G", 0x0B => "H", 0x0C => "I", 0x0D => "J",
        0x0E => "K", 0x0F => "L", 0x10 => "M", 0x11 => "N", 0x12 => "O",
        0x13 => "P", 0x14 => "Q", 0x15 => "R", 0x16 => "S", 0x17 => "T",
        0x18 => "U", 0x19 => "V", 0x1A => "W", 0x1B => "X", 0x1C => "Y",
        0x1D => "Z",
        0x1E => "1", 0x1F => "2", 0x20 => "3", 0x21 => "4", 0x22 => "5",
        0x23 => "6", 0x24 => "7", 0x25 => "8", 0x26 => "9", 0x27 => "0",
        0x28 => "Enter", 0x29 => "Esc", 0x2A => "Bksp", 0x2B => "Tab",
        0x2C => "Space", 0x2D => "-", 0x2E => "=", 0x2F => "[", 0x30 => "]",
        0x31 => "\\",
        0x33 => ";", 0x34 => "'", 0x35 => "`",
        0x36 => ",", 0x37 => ".", 0x38 => "/",
        _ => "?",
    }
}

fn parse_args() -> PathBuf {
    let args: Vec<String> = std::env::args().collect();
    let mut output_dir = PathBuf::from(".");
    let mut iter = args.iter().skip(1);
    while let Some(a) = iter.next() {
        match a.as_str() {
            "-o" | "--output-dir" => {
                if let Some(v) = iter.next() {
                    output_dir = PathBuf::from(v);
                }
            }
            "-h" | "--help" => {
                eprintln!(
                    "Usage: key_recorder [--output-dir DIR]\n\
                     \n\
                     Each press (depth crossing 0.5 upward) writes a CSV with\n\
                     1000 pre-trigger + 7000 post-trigger samples at ~8 kHz.\n\
                     \n\
                     Env: WOOTING_ANALOG_SDK_PATH (default {})",
                    DEFAULT_SDK_PATH
                );
                std::process::exit(0);
            }
            _ => {}
        }
    }
    output_dir
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::try_init().ok();
    let output_dir = parse_args();
    let sdk_path = std::env::var("WOOTING_ANALOG_SDK_PATH")
        .unwrap_or_else(|_| DEFAULT_SDK_PATH.to_string());

    println!("Loading SDK from: {}", sdk_path);
    let sdk = AnalogSdk::open(Path::new(&sdk_path))?;
    let count = sdk
        .initialise()
        .map_err(|rc| format!("Analog SDK initialise() returned {rc}"))?;
    println!("SDK initialised; {} device(s) connected.", count);
    sdk.set_keycode_mode_hid()
        .map_err(|rc| format!("set_keycode_mode_hid returned {rc}"))?;
    for (vid, pid, name) in sdk.list_devices() {
        println!("  vid=0x{:04X} pid=0x{:04X} name={:?}", vid, pid, name);
    }

    if count == 0 {
        eprintln!();
        eprintln!("⚠️  No Wooting device found by the SDK.");
        eprintln!();
        eprintln!("Most common causes:");
        eprintln!(
            "  - pitchgrid-mapper's dev server (./run_dev.sh) is running and \
             has the keyboard open."
        );
        eprintln!("  - Wootility (the desktop app) is running and holds the device.");
        eprintln!("  - The SDK plugin isn't installed at");
        eprintln!("    /usr/local/share/WootingAnalogPlugins/.");
        eprintln!();
        eprintln!(
            "Stop the other process and re-run. The recorder will keep \
             polling but won't produce any data until a device appears."
        );
        eprintln!();
    }

    let abs_output = std::fs::canonicalize(&output_dir).unwrap_or_else(|_| {
        // Fallback: join with cwd manually if the dir doesn't exist yet.
        std::env::current_dir()
            .map(|c| c.join(&output_dir))
            .unwrap_or_else(|_| output_dir.clone())
    });
    println!(
        "Recording to {} — press keys to capture (Ctrl-C to stop).",
        abs_output.display()
    );
    create_dir_all(&output_dir)?;

    let start = Instant::now();
    let mut recorders: HashMap<u16, KeyRecorder> = HashMap::new();
    let mut keycodes: Vec<u16> = vec![0; READ_BUFFER_CAP];
    let mut depths: Vec<f32> = vec![0.0; READ_BUFFER_CAP];

    // Once a key has been seen at least once, we keep sampling it on every
    // tick (synthesizing 0.0 when the SDK omits it from read_full_buffer).
    // This way the second press of any key gets a full 1000-sample
    // pre-trigger window of real noise-floor data.
    let mut ever_seen: HashSet<u16> = HashSet::new();

    // Diagnostics: heartbeat every second and edge-detection prints.
    let mut last_heartbeat = Instant::now();
    let mut tick_count: u64 = 0;
    let mut active_count_max: usize = 0;
    let mut written_count: u64 = 0;

    loop {
        let next_deadline = Instant::now() + POLL_INTERVAL;

        let n = sdk.read_full_buffer(&mut keycodes, &mut depths).unwrap_or(0);
        let t_us = start.elapsed().as_micros() as u64;
        active_count_max = active_count_max.max(n);

        let mut this_tick: HashSet<u16> = HashSet::with_capacity(n);
        for i in 0..n {
            let kc = keycodes[i];
            let depth = depths[i];
            this_tick.insert(kc);

            let was_new = ever_seen.insert(kc);
            if was_new {
                println!(
                    "  [t={:.3}s] new key 0x{:02X} ({}) first seen at depth={:.3}",
                    t_us as f64 / 1_000_000.0,
                    kc,
                    label_for(kc),
                    depth
                );
            }

            let rec = recorders.entry(kc).or_insert_with(KeyRecorder::new);
            let prev_depth = rec.last_depth;
            let was_recording = rec.trigger_global_idx.is_some();
            let result = rec.push(Sample { t_us, depth });

            // Edge logs.
            if !was_recording
                && prev_depth < TRIGGER_THRESHOLD
                && depth >= TRIGGER_THRESHOLD
            {
                println!(
                    "  [t={:.3}s] TRIGGER 0x{:02X} ({}) depth {:.3} → {:.3}",
                    t_us as f64 / 1_000_000.0,
                    kc,
                    label_for(kc),
                    prev_depth,
                    depth
                );
            }

            if let Some(snap) = result {
                match write_csv(&output_dir, kc, &snap) {
                    Ok(p) => {
                        written_count += 1;
                        println!("  [t={:.3}s] WROTE {}", t_us as f64 / 1_000_000.0, p.display());
                    }
                    Err(e) => eprintln!("  CSV write failed: {e}"),
                }
            }
        }

        // For every key we've ever seen but isn't in this tick, synthesize a
        // 0.0 sample. This drives both the FSM completion (so the post-trigger
        // window doesn't stall when the user lets go) and the noise-floor
        // pre-trigger window for subsequent presses.
        let synth_keys: Vec<u16> = ever_seen.difference(&this_tick).copied().collect();
        for kc in synth_keys {
            if let Some(rec) = recorders.get_mut(&kc) {
                if let Some(snap) = rec.push(Sample { t_us, depth: 0.0 }) {
                    match write_csv(&output_dir, kc, &snap) {
                        Ok(p) => {
                            written_count += 1;
                            println!(
                                "  [t={:.3}s] WROTE {}",
                                t_us as f64 / 1_000_000.0,
                                p.display()
                            );
                        }
                        Err(e) => eprintln!("  CSV write failed: {e}"),
                    }
                }
            }
        }

        tick_count += 1;
        if last_heartbeat.elapsed() >= Duration::from_secs(2) {
            let live_devices = sdk.list_devices().len();
            println!(
                "  [heartbeat t={:.1}s] {} ticks, devices={}, tracking {} key(s), max active in tick: {}, files written: {}",
                t_us as f64 / 1_000_000.0,
                tick_count,
                live_devices,
                ever_seen.len(),
                active_count_max,
                written_count
            );
            if live_devices == 0 && tick_count > 100 {
                eprintln!(
                    "    !! still no device — is run_dev.sh / Wootility / another \
                     process holding the keyboard?"
                );
            }
            active_count_max = 0;
            last_heartbeat = Instant::now();
        }

        let now = Instant::now();
        if now < next_deadline {
            spin_sleep::sleep(next_deadline - now);
        }
    }

    // Unreachable in normal flow but keeps the SDK shutdown explicit if
    // someone wires a clean exit later.
    #[allow(unreachable_code)]
    {
        sdk.uninitialise();
        Ok(())
    }
}
