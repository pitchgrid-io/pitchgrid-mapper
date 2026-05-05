/// Runtime ctypes-style wrapper around `libwooting_analog_sdk.dylib`.

use std::os::raw::{c_char, c_float, c_int, c_uint, c_ushort};
use std::path::Path;

use libloading::{Library, Symbol};

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct DeviceInfoFFI {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer_name: *const c_char,
    pub device_name: *const c_char,
    pub device_id: u64,
    pub device_type: c_int,
}

#[derive(Debug, Clone)]
pub struct DeviceInfo {
    pub vendor_id: u16,
    pub product_id: u16,
    pub manufacturer_name: String,
    pub device_name: String,
    pub device_id: u64,
    pub device_type: i32,
}

pub struct AnalogSdk {
    _lib: Library,
    initialise: unsafe extern "C" fn() -> c_int,
    uninitialise: unsafe extern "C" fn() -> c_int,
    set_keycode_mode: unsafe extern "C" fn(c_uint) -> c_int,
    read_full_buffer: unsafe extern "C" fn(*mut c_ushort, *mut c_float, c_uint) -> c_int,
    get_connected_devices_info:
        unsafe extern "C" fn(*mut *const DeviceInfoFFI, c_uint) -> c_int,
    is_initialised: unsafe extern "C" fn() -> bool,
}

unsafe fn load_fn<T: Copy>(lib: &Library, name: &[u8]) -> Result<T, String> {
    let s: Symbol<T> = lib.get(name).map_err(|e| e.to_string())?;
    Ok(*s)
}

unsafe impl Send for AnalogSdk {}
unsafe impl Sync for AnalogSdk {}

impl AnalogSdk {
    pub fn open(dylib_path: &Path) -> Result<Self, String> {
        let lib = unsafe { Library::new(dylib_path) }
            .map_err(|e| format!("dlopen failed: {e}"))?;
        let initialise = unsafe { load_fn(&lib, b"wooting_analog_initialise")? };
        let uninitialise = unsafe { load_fn(&lib, b"wooting_analog_uninitialise")? };
        let set_keycode_mode = unsafe { load_fn(&lib, b"wooting_analog_set_keycode_mode")? };
        let read_full_buffer = unsafe { load_fn(&lib, b"wooting_analog_read_full_buffer")? };
        let get_connected_devices_info =
            unsafe { load_fn(&lib, b"wooting_analog_get_connected_devices_info")? };
        let is_initialised = unsafe { load_fn(&lib, b"wooting_analog_is_initialised")? };
        Ok(Self {
            _lib: lib,
            initialise,
            uninitialise,
            set_keycode_mode,
            read_full_buffer,
            get_connected_devices_info,
            is_initialised,
        })
    }

    pub fn initialise(&self) -> Result<u32, i32> {
        let rc = unsafe { (self.initialise)() };
        if rc < 0 {
            Err(rc)
        } else {
            Ok(rc as u32)
        }
    }

    pub fn uninitialise(&self) {
        unsafe {
            (self.uninitialise)();
        }
    }

    pub fn is_initialised(&self) -> bool {
        unsafe { (self.is_initialised)() }
    }

    /// Set keycode mode. 0=HID, 1=ScanCode1, 2=VirtualKey, 3=VirtualKeyTranslate.
    pub fn set_keycode_mode_hid(&self) -> Result<(), i32> {
        let rc = unsafe { (self.set_keycode_mode)(0) };
        if rc < 0 {
            Err(rc)
        } else {
            Ok(())
        }
    }

    pub fn read_full_buffer(
        &self,
        keycodes: &mut [u16],
        depths: &mut [f32],
    ) -> Result<usize, i32> {
        let cap = keycodes.len().min(depths.len()) as c_uint;
        let rc = unsafe {
            (self.read_full_buffer)(keycodes.as_mut_ptr(), depths.as_mut_ptr(), cap)
        };
        if rc < 0 {
            Err(rc)
        } else {
            Ok(rc as usize)
        }
    }

    pub fn get_connected_devices(&self) -> Vec<DeviceInfo> {
        let mut buf: Vec<*const DeviceInfoFFI> = vec![std::ptr::null(); 16];
        let n = unsafe {
            (self.get_connected_devices_info)(buf.as_mut_ptr(), buf.len() as c_uint)
        };
        if n <= 0 {
            return Vec::new();
        }
        let mut out = Vec::with_capacity(n as usize);
        for i in 0..n as usize {
            let p = buf[i];
            if p.is_null() {
                continue;
            }
            unsafe {
                let info = *p;
                let mfr = if info.manufacturer_name.is_null() {
                    String::new()
                } else {
                    std::ffi::CStr::from_ptr(info.manufacturer_name)
                        .to_string_lossy()
                        .into_owned()
                };
                let name = if info.device_name.is_null() {
                    String::new()
                } else {
                    std::ffi::CStr::from_ptr(info.device_name)
                        .to_string_lossy()
                        .into_owned()
                };
                out.push(DeviceInfo {
                    vendor_id: info.vendor_id,
                    product_id: info.product_id,
                    manufacturer_name: mfr,
                    device_name: name,
                    device_id: info.device_id,
                    device_type: info.device_type as i32,
                });
            }
        }
        out
    }
}
