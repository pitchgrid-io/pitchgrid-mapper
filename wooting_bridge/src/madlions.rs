//! Madlions-family Hall-effect board support over the `0xFF60` vendor interface.
//!
//! Unlike Wooting boards (read through the Analog SDK), these keyboards do not
//! stream analog — they *answer* per-key travel poll requests. We poll
//! `02 96 1C` (4 keys/request, the firmware caps at 4) and normalise the
//! big-endian travel (0..=350) to 0..=1. RGB is written as an 80-slot frame on
//! the same interface. Both share one HID handle (serialised by the caller).

use std::collections::HashMap;

use hidapi::{HidApi, HidDevice};

pub const MADLIONS_VID: u16 = 0x373B;
const RGB_USAGE_PAGE: u16 = 0xFF60;
const RGB_USAGE: u16 = 0x61;
pub const TRAVEL_FULL: f32 = 350.0;
const REPORT_LEN: usize = 33;
pub const KEYS_PER_POLL: u16 = 4;
pub const NUM_RGB_SLOTS: usize = 80;

pub struct MadlionsDevice {
    dev: HidDevice,
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

        // Precompute the set of 4-key chunk offsets we need to cover.
        let mut chunk_set: Vec<u8> = index_to_keycode
            .keys()
            .map(|&i| ((i / KEYS_PER_POLL) * KEYS_PER_POLL) as u8)
            .collect();
        chunk_set.sort_unstable();
        chunk_set.dedup();

        Ok(Self {
            dev,
            index_to_keycode,
            chunks: chunk_set,
            xy_to_slot,
        })
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
