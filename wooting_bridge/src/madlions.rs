//! Madlions-family Hall-effect board support over the `0xFF60` vendor interface.
//!
//! Unlike Wooting boards (read through the Analog SDK), these keyboards do not
//! stream analog — they *answer* per-key travel poll requests (`02 96 1C`,
//! 4 keys/request, the firmware caps it at 4). The firmware queues pipelined
//! requests and answers them in order (verified on-device: send N, read N,
//! ~0.33 ms per response vs ~0.54 ms serialized), so `poll_chunks` fans out.
//! Onset comes from the digital NKRO interface (report `0x06`, interrupt-
//! driven) with the actuation point forced to its 0.1 mm firmware minimum
//! while the bridge is active (`03 96 0D`; saved and restored on exit).
//! RGB is written as an 80-slot frame on the same `0xFF60` handle.

use std::collections::HashMap;
use std::time::Instant;

use hidapi::{HidApi, HidDevice};

pub const MADLIONS_VID: u16 = 0x373B;
const RGB_USAGE_PAGE: u16 = 0xFF60;
const RGB_USAGE: u16 = 0x61;
pub const TRAVEL_FULL: f32 = 350.0;
const REPORT_LEN: usize = 33;
pub const KEYS_PER_POLL: u16 = 4;
pub const NUM_RGB_SLOTS: usize = 80;

// Command bytes shared by the HE config protocol (from the community
// reverse-engineering of the FGG web configurator, verified on-device).
const CMD_READ: u8 = 0x02;
const CMD_WRITE: u8 = 0x03;
const SUBCMD_HE: u8 = 0x96;
const OP_TRAVEL: u8 = 0x1C;
const OP_ACTUATION: u8 = 0x0D;
const OP_RAPID_TRIGGER: u8 = 0x0E;

/// Actuation array geometry: 77 slots, of which 0..=69 are real keys.
pub const ACT_SLOTS: usize = 77;
const ACT_REAL: usize = 70;
const ACT_WRITE_STARTS: [u8; 7] = [0, 11, 22, 33, 44, 55, 66];
const ACT_READ_STARTS: [u8; 6] = [0x00, 0x0C, 0x18, 0x24, 0x30, 0x3C];
/// Firmware minimum actuation: 0.1 mm (raw 0.01 mm units). This is the onset
/// depth we force while the bridge is active so the NKRO report fires at the
/// very top of the key's travel.
pub const ACTUATION_ONSET_RAW: u16 = 10;

/// Rapid-trigger array geometry: 72 slots, of which 0..=69 are real keys.
pub const RT_SLOTS: usize = 72;
const RT_REAL: usize = 70;

pub struct MadlionsDevice {
    dev: HidDevice,
    /// Digital NKRO keyboard interface (report 0x06 key bitmap), opened
    /// best-effort for instant key-down onset detection. None if it couldn't
    /// be opened — analog background polling then handles onset on its own.
    nkro: Option<HidDevice>,
    /// analog poll index -> HID keycode (from the soup MAD68HE layout).
    pub index_to_keycode: HashMap<u16, u16>,
    /// distinct 4-key chunk offsets covering every mapped index.
    pub chunks: Vec<u8>,
    /// (logical x, y) -> RGB wire slot, for colour output.
    pub xy_to_slot: HashMap<(i16, i16), u16>,
}

impl MadlionsDevice {
    /// Open the `0xFF60` interface of the Madlions board with `product_id`.
    pub fn open(
        product_id: u16,
        index_to_keycode: HashMap<u16, u16>,
        xy_to_slot: HashMap<(i16, i16), u16>,
    ) -> Result<Self, String> {
        // Use the same init mode as the wooting-analog-sdk: device discovery
        // disabled. The hidapi context is a process-global singleton shared
        // with the SDK; calling `HidApi::new()` (which enables discovery) would
        // make the SDK's later `disable_device_discovery()` panic. With
        // discovery disabled we must list devices explicitly per VID.
        #[allow(deprecated)]
        let mut api = HidApi::new_without_enumerate().map_err(|e| format!("hidapi init: {e}"))?;
        // Discovery is disabled, so populate the device list for our VID explicitly.
        let _ = api.add_devices(MADLIONS_VID, 0);
        let candidates: Vec<_> = api
            .device_list()
            .filter(|d| d.vendor_id() == MADLIONS_VID && d.product_id() == product_id)
            .collect();
        // Prefer the 0xFF60/0x61 vendor collection; fall back to a usage_page
        // match alone, then (if the backend reports no usages) the lone
        // interface that isn't a standard keyboard/mouse page.
        let chosen = candidates
            .iter()
            .find(|d| d.usage_page() == RGB_USAGE_PAGE && d.usage() == RGB_USAGE)
            .or_else(|| candidates.iter().find(|d| d.usage_page() == RGB_USAGE_PAGE))
            .or_else(|| {
                candidates
                    .iter()
                    .find(|d| d.usage_page() >= 0xFF00 || d.interface_number() == 1)
            });
        let path = chosen
            .map(|d| d.path().to_owned())
            .ok_or_else(|| {
                let seen: Vec<String> = candidates
                    .iter()
                    .map(|d| {
                        format!(
                            "[up={:#06x} u={:#06x} if={}]",
                            d.usage_page(),
                            d.usage(),
                            d.interface_number()
                        )
                    })
                    .collect();
                format!(
                    "Madlions 0xFF60 interface not found (vid=373b pid={product_id:04x}); \
                     saw: {}",
                    seen.join(" ")
                )
            })?;
        let dev = api
            .open_path(&path)
            .map_err(|e| format!("open 0xFF60: {e}"))?;

        // Best-effort: open the digital keyboard interface for NKRO onset
        // hints. On this board the key bitmap (report 0x06) is delivered by the
        // non-vendor interface; any of its collections that opens surfaces it.
        // If none opens we simply fall back to analog-only onset detection.
        let nkro = candidates
            .iter()
            .filter(|d| d.usage_page() != RGB_USAGE_PAGE)
            .find_map(|d| api.open_path(d.path()).ok());
        log::info!(
            "Madlions NKRO onset interface: {}",
            if nkro.is_some() { "opened" } else { "unavailable (analog-only onset)" }
        );

        // Precompute the set of 4-key chunk offsets we need to cover.
        let mut chunk_set: Vec<u8> = index_to_keycode
            .keys()
            .map(|&i| ((i / KEYS_PER_POLL) * KEYS_PER_POLL) as u8)
            .collect();
        chunk_set.sort_unstable();
        chunk_set.dedup();

        Ok(Self {
            dev,
            nkro,
            index_to_keycode,
            chunks: chunk_set,
            xy_to_slot,
        })
    }

    /// Whether the digital NKRO onset interface is open.
    pub fn has_nkro(&self) -> bool {
        self.nkro.is_some()
    }

    /// Drain pending input from the NKRO interface; if a report-0x06 key bitmap
    /// arrived, copy its 240-bit key area into `bitmap` (byte k = HID usages
    /// 8k..8k+7) and return true. This digital "which keys are down" view lets
    /// the poll loop start velocity-sampling the instant a key actuates, ahead
    /// of the analog background scan. No-op (false) when NKRO isn't available.
    pub fn read_nkro(&self, bitmap: &mut [u8; 32]) -> bool {
        let Some(dev) = self.nkro.as_ref() else {
            return false;
        };
        let mut buf = [0u8; 64];
        let mut updated = false;
        while let Ok(n) = dev.read_timeout(&mut buf, 0) {
            if n == 0 {
                break;
            }
            // buf[0]=report id (6), buf[1]=modifiers, buf[2..]=key bitmap.
            if buf[0] == 0x06 && n >= 3 {
                let copy = (n - 2).min(32);
                bitmap[..copy].copy_from_slice(&buf[2..2 + copy]);
                for b in &mut bitmap[copy..] {
                    *b = 0;
                }
                updated = true;
            }
        }
        updated
    }

    /// Like `read_nkro`, but blocks up to `first_timeout_ms` for the first
    /// report, then drains whatever else queued so `bitmap` reflects the most
    /// recent state. Blocking here costs nothing: the NKRO interface is a
    /// separate HID handle, and when nothing is pressed there is nothing else
    /// for the poll loop to do — this is what turns the loop interrupt-driven.
    pub fn read_nkro_timeout(&self, bitmap: &mut [u8; 32], first_timeout_ms: i32) -> bool {
        let Some(dev) = self.nkro.as_ref() else {
            return false;
        };
        let mut buf = [0u8; 64];
        let mut updated = false;
        let mut timeout = first_timeout_ms;
        while let Ok(n) = dev.read_timeout(&mut buf, timeout) {
            if n == 0 {
                break;
            }
            timeout = 0; // only the first read blocks
            if buf[0] == 0x06 && n >= 3 {
                let copy = (n - 2).min(32);
                bitmap[..copy].copy_from_slice(&buf[2..2 + copy]);
                for b in &mut bitmap[copy..] {
                    *b = 0;
                }
                updated = true;
            }
        }
        updated
    }

    /// Poll 4 keys starting at `offset`; returns raw travel (0..=350) per key.
    /// Requesting more than 4 keys makes the firmware return all zeros.
    ///
    /// Drains any stale input first and validates the response echoes this
    /// request's `02 96 1C <offset>` header, so a delayed prior reply can never
    /// be misattributed to the wrong keys (which would spawn phantom notes).
    pub fn poll_chunk(&self, offset: u8) -> Option<[u16; 4]> {
        let mut buf = [0u8; 64];
        // Drain pending input (non-blocking) to resync request/response.
        while matches!(self.dev.read_timeout(&mut buf, 0), Ok(n) if n > 0) {}

        let mut req = [0u8; REPORT_LEN];
        req[1] = 0x02; // CMD_READ
        req[2] = 0x96; // SUBCMD_HE
        req[3] = 0x1C; // live travel
        req[7] = offset;
        req[8] = KEYS_PER_POLL as u8;
        self.dev.write(&req).ok()?;
        let n = self.dev.read_timeout(&mut buf, 20).ok()?;
        if n < 27 {
            return None;
        }
        // Reject anything that isn't the travel reply for THIS offset.
        if buf[0] != 0x02 || buf[1] != 0x96 || buf[2] != 0x1C || buf[6] != offset {
            return None;
        }
        let mut out = [0u16; 4];
        for (j, slot) in out.iter_mut().enumerate() {
            let b = 10 + j * 5;
            *slot = (u16::from(buf[b]) << 8) | u16::from(buf[b + 1]);
        }
        Some(out)
    }

    /// Pipelined travel poll: send all requests back-to-back, then read the
    /// responses. The firmware queues requests and answers them in order
    /// (verified: 0/1600 out-of-order over 4-deep pipelines; ~0.33 ms per
    /// response vs ~0.54 ms serialized), so N chunks cost roughly one
    /// round-trip plus N-1 response gaps instead of N full round-trips.
    ///
    /// Each returned entry carries the `Instant` its response was read, so
    /// velocity fitting sees true per-sample timestamps even when a batch
    /// spans more than a millisecond. On any out-of-order response the
    /// remaining reads are abandoned and the queue drained (a partial batch
    /// with correct attribution beats a full batch with phantom notes).
    pub fn poll_chunks(&self, offsets: &[u8]) -> Vec<(u8, [u16; 4], Instant)> {
        let mut buf = [0u8; 64];
        // Resync: drop any stale responses from a previous, aborted batch.
        while matches!(self.dev.read_timeout(&mut buf, 0), Ok(n) if n > 0) {}

        let mut sent = 0usize;
        for &off in offsets {
            let mut req = [0u8; REPORT_LEN];
            req[1] = CMD_READ;
            req[2] = SUBCMD_HE;
            req[3] = OP_TRAVEL;
            req[7] = off;
            req[8] = KEYS_PER_POLL as u8;
            if self.dev.write(&req).is_err() {
                break;
            }
            sent += 1;
        }

        let mut out = Vec::with_capacity(sent);
        for &expected in &offsets[..sent] {
            let Ok(n) = self.dev.read_timeout(&mut buf, 15) else {
                break;
            };
            if n < 27 {
                break;
            }
            if buf[0] != CMD_READ || buf[1] != SUBCMD_HE || buf[2] != OP_TRAVEL
                || buf[6] != expected
            {
                // Desync — drain and bail with what we have.
                while matches!(self.dev.read_timeout(&mut buf, 0), Ok(n) if n > 0) {}
                break;
            }
            let t = Instant::now();
            let mut travels = [0u16; 4];
            for (j, slot) in travels.iter_mut().enumerate() {
                let b = 10 + j * 5;
                *slot = (u16::from(buf[b]) << 8) | u16::from(buf[b + 1]);
            }
            out.push((expected, travels, t));
        }
        out
    }

    /// Write a config request and wait for the matching `02 96 <opcode>`
    /// response, skipping write-echo ACKs (`03 96 ...`) and unrelated reports.
    fn he_read_response(&self, req: &[u8; REPORT_LEN], opcode: u8, buf: &mut [u8; 64]) -> bool {
        while matches!(self.dev.read_timeout(buf, 0), Ok(n) if n > 0) {}
        if self.dev.write(req).is_err() {
            return false;
        }
        for _ in 0..8 {
            match self.dev.read_timeout(buf, 40) {
                Ok(n) if n >= 8 => {
                    if buf[0] == CMD_READ && buf[1] == SUBCMD_HE && buf[2] == opcode {
                        return true;
                    }
                    // Not ours (echo/ACK or a stray travel reply) — keep reading.
                }
                _ => return false,
            }
        }
        false
    }

    /// Read the per-key actuation array (raw 0.01 mm units, `ACT_SLOTS` long).
    /// None if any chunk fails — callers must not restore a partial array.
    pub fn read_actuation(&self) -> Option<Vec<u16>> {
        let mut arr = vec![0u16; ACT_SLOTS];
        let mut buf = [0u8; 64];
        for &start in &ACT_READ_STARTS {
            let mut req = [0u8; REPORT_LEN];
            req[1] = CMD_READ;
            req[2] = SUBCMD_HE;
            req[3] = OP_ACTUATION;
            req[7] = start;
            req[8] = 0x0C; // 12 entries per read chunk
            if !self.he_read_response(&req, OP_ACTUATION, &mut buf) {
                return None;
            }
            // Response: data[6]=start, data[7]=count, entries u16be from data[8].
            let (rs, count) = (buf[6] as usize, buf[7] as usize);
            for k in 0..count {
                let b = 8 + k * 2;
                if rs + k < ACT_SLOTS && b + 1 < 64 {
                    arr[rs + k] = (u16::from(buf[b]) << 8) | u16::from(buf[b + 1]);
                }
            }
        }
        Some(arr)
    }

    /// Write the full per-key actuation array (7 chunks; the last chunk's flag
    /// 0x02 applies the change). Byte layout per the FGG capture.
    pub fn write_actuation(&self, arr: &[u16]) -> bool {
        let last = ACT_WRITE_STARTS.len() - 1;
        for (ci, &start) in ACT_WRITE_STARTS.iter().enumerate() {
            let mut pkt = [0u8; REPORT_LEN];
            pkt[1] = CMD_WRITE;
            pkt[2] = SUBCMD_HE;
            pkt[3] = OP_ACTUATION;
            pkt[7] = start;
            pkt[8] = 0x0B; // 11 entries per write chunk
            pkt[9] = if ci == 0 { 0x01 } else if ci == last { 0x02 } else { 0x00 };
            for k in 0..11usize {
                let idx = start as usize + k;
                let v = if idx < arr.len() { arr[idx] } else { 0 };
                pkt[10 + k * 2] = (v >> 8) as u8;
                pkt[11 + k * 2] = v as u8;
            }
            if self.dev.write(&pkt).is_err() {
                return false;
            }
        }
        // The board echoes each write chunk as an ACK; clear them out.
        let mut buf = [0u8; 64];
        while matches!(self.dev.read_timeout(&mut buf, 5), Ok(n) if n > 0) {}
        true
    }

    /// Write a uniform actuation depth to every real key (pads stay 0).
    pub fn write_actuation_uniform(&self, raw: u16) -> bool {
        let mut arr = vec![0u16; ACT_SLOTS];
        arr[..ACT_REAL].fill(raw);
        self.write_actuation(&arr)
    }

    /// Read the per-key rapid-trigger array: (enable, reset_raw, rapid_raw)
    /// per slot. None if any chunk fails.
    pub fn read_rapid_trigger(&self) -> Option<Vec<(u8, u16, u16)>> {
        let mut arr = vec![(0u8, 0u16, 0u16); RT_SLOTS];
        let mut buf = [0u8; 64];
        for start in (0..RT_SLOTS as u8).step_by(4) {
            let mut req = [0u8; REPORT_LEN];
            req[1] = CMD_READ;
            req[2] = SUBCMD_HE;
            req[3] = OP_RAPID_TRIGGER;
            req[7] = start;
            req[8] = 0x04;
            if !self.he_read_response(&req, OP_RAPID_TRIGGER, &mut buf) {
                return None;
            }
            let (rs, count) = (buf[6] as usize, buf[7] as usize);
            for e in 0..count {
                let b = 8 + e * 5;
                if rs + e < RT_SLOTS && b + 4 < 64 {
                    arr[rs + e] = (
                        buf[b],
                        (u16::from(buf[b + 1]) << 8) | u16::from(buf[b + 2]),
                        (u16::from(buf[b + 3]) << 8) | u16::from(buf[b + 4]),
                    );
                }
            }
        }
        Some(arr)
    }

    /// Write the full rapid-trigger array (18 chunks of 4; last flag applies).
    pub fn write_rapid_trigger(&self, arr: &[(u8, u16, u16)]) -> bool {
        let starts: Vec<u8> = (0..RT_SLOTS as u8).step_by(4).collect();
        let last = starts.len() - 1;
        for (ci, &start) in starts.iter().enumerate() {
            let mut pkt = [0u8; REPORT_LEN];
            pkt[1] = CMD_WRITE;
            pkt[2] = SUBCMD_HE;
            pkt[3] = OP_RAPID_TRIGGER;
            pkt[7] = start;
            pkt[8] = 0x04;
            pkt[9] = if ci == 0 { 0x01 } else if ci == last { 0x02 } else { 0x00 };
            for e in 0..4usize {
                let idx = start as usize + e;
                let (en, reset, rapid) = if idx < arr.len() { arr[idx] } else { (0, 0, 0) };
                let b = 10 + e * 5;
                pkt[b] = en;
                pkt[b + 1] = (reset >> 8) as u8;
                pkt[b + 2] = reset as u8;
                pkt[b + 3] = (rapid >> 8) as u8;
                pkt[b + 4] = rapid as u8;
            }
            if self.dev.write(&pkt).is_err() {
                return false;
            }
        }
        let mut buf = [0u8; 64];
        while matches!(self.dev.read_timeout(&mut buf, 5), Ok(n) if n > 0) {}
        true
    }

    /// Disable rapid-trigger on every real key. Rapid-trigger re-fires the
    /// digital key state on micro-movements — musically that would fake
    /// note-offs mid-press and fight the NKRO onset logic, so the bridge
    /// turns it off while active (restored on exit). Travels are left at the
    /// firmware default 0.50 mm so a stale enable bit behaves sanely.
    pub fn write_rapid_trigger_disabled(&self) -> bool {
        let mut arr = vec![(0u8, 50u16, 50u16); RT_SLOTS];
        for pad in arr.iter_mut().skip(RT_REAL) {
            *pad = (0, 0, 0);
        }
        self.write_rapid_trigger(&arr)
    }

    /// Send an 80-slot RGB frame followed by the commit packet.
    pub fn write_rgb(&self, slots: &[(u8, u8, u8); NUM_RGB_SLOTS]) -> bool {
        let mut idx = 0usize;
        for chunk in 0..5u8 {
            for sub in [0x00u8, 0x08u8] {
                let mut pkt = [0u8; REPORT_LEN];
                pkt[1] = 0x07; // REPORT_ID
                pkt[2] = 0x42; // CMD_SET_COLORS
                pkt[3] = chunk;
                pkt[4] = sub;
                pkt[5] = 8; // keys per packet
                for k in 0..8 {
                    let (r, g, b) = slots[idx];
                    pkt[6 + k * 3] = r;
                    pkt[7 + k * 3] = g;
                    pkt[8 + k * 3] = b;
                    idx += 1;
                }
                if self.dev.write(&pkt).is_err() {
                    return false;
                }
            }
        }
        let mut commit = [0u8; REPORT_LEN];
        commit[1] = 0x07; // REPORT_ID
        commit[2] = 0x41; // CMD_COMMIT
        commit[3] = 0x01;
        commit[5] = 0x90;
        commit[6] = 0xFF;
        commit[8] = 0xEE;
        commit[9] = 0xD2;
        self.dev.write(&commit).is_ok()
    }
}
