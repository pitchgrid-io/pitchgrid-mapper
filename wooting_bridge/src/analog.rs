//! Wooting Analog SDK wrapper.
//!
//! The SDK is linked at compile time via the `wooting-analog-sdk` crate —
//! no more runtime `dlopen` of `libwooting_analog_sdk.dylib`. The HID-side
//! plugin (e.g. AnalogSense's `abiv1.dylib`) is still loaded at runtime
//! from whichever directory the host passes to `initialise`, so the
//! installer can bundle it inside the `.app`'s Resources.

use std::path::Path;

use wooting_analog_sdk::sdk::AnalogSDK;
use wooting_analog_sdk::{
    DeviceInfo as SdkDeviceInfo, KeycodeType, WootingAnalogResult,
};

#[derive(Debug, Clone)]
pub struct DeviceInfo {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer_name: String,
    pub device_name: String,
    pub device_id: u64,
    pub device_type: i32,
}

impl From<SdkDeviceInfo> for DeviceInfo {
    fn from(d: SdkDeviceInfo) -> Self {
        Self {
            vendor_id: d.vendor_id,
            product_id: d.product_id,
            manufacturer_name: d.manufacturer_name,
            device_name: d.device_name,
            device_id: d.device_id,
            device_type: d.device_type as i32,
        }
    }
}

pub struct AnalogSdk {
    sdk: AnalogSDK,
}

// AnalogSDK already declares `unsafe impl Send`; we only ever touch it
// from the poll thread but expose it through the same Arc<BridgeInner>
// the RGB / lifecycle code uses.
unsafe impl Sync for AnalogSdk {}

impl AnalogSdk {
    /// Construct an uninitialised SDK instance with the keycode mode set to
    /// HID Usage IDs (what our YAML labels resolve to via
    /// `HID_USAGE_BY_LABEL`). Call `initialise(plugin_dir)` to actually
    /// load plugins and talk to the keyboard.
    pub fn new() -> Self {
        let mut sdk = AnalogSDK::new();
        sdk.keycode_mode = KeycodeType::HID;
        Self { sdk }
    }

    pub fn initialise(&mut self, plugin_dir: &Path) -> Result<u32, i32> {
        let dir = plugin_dir.to_string_lossy();
        match self.sdk.initialise_with_plugin_path(&dir, true).0 {
            Ok(count) => Ok(count),
            Err(err) => Err(err as i32),
        }
    }

    pub fn uninitialise(&mut self) {
        self.sdk.unload();
    }

    pub fn is_initialised(&self) -> bool {
        self.sdk.initialised
    }

    /// Keycode mode is already HID after `new()`; this remains for parity
    /// with the previous dlopen API the bridge expected.
    pub fn set_keycode_mode_hid(&mut self) -> Result<(), i32> {
        self.sdk.keycode_mode = KeycodeType::HID;
        Ok(())
    }

    /// Drain the SDK's full-buffer read into the caller's pre-allocated
    /// arrays. Returns the count actually written (0 if nothing pressed).
    pub fn read_full_buffer(
        &mut self,
        keycodes: &mut [u16],
        depths: &mut [f32],
    ) -> Result<usize, i32> {
        let cap = keycodes.len().min(depths.len());
        if cap == 0 {
            return Ok(0);
        }
        match self.sdk.read_full_buffer(cap, 0).0 {
            Ok(map) => {
                let mut n = 0;
                for (k, v) in map.into_iter() {
                    if n >= cap {
                        break;
                    }
                    keycodes[n] = k;
                    depths[n] = v;
                    n += 1;
                }
                Ok(n)
            }
            Err(WootingAnalogResult::NoDevices) => Ok(0),
            Err(err) => Err(err as i32),
        }
    }

    pub fn get_connected_devices(&mut self) -> Vec<DeviceInfo> {
        match self.sdk.get_device_info().0 {
            Ok(devs) => devs.into_iter().map(DeviceInfo::from).collect(),
            Err(_) => Vec::new(),
        }
    }
}
