/// Runtime ctypes-style wrapper around `libwooting-rgb-sdk.dylib`.

use std::os::raw::c_uchar;
use std::path::Path;

use libloading::{Library, Symbol};

pub struct RgbSdk {
    _lib: Library,
    kbd_connected: unsafe extern "C" fn() -> bool,
    array_set_single:
        unsafe extern "C" fn(c_uchar, c_uchar, c_uchar, c_uchar, c_uchar) -> bool,
    array_update_keyboard: unsafe extern "C" fn() -> bool,
    array_auto_update: unsafe extern "C" fn(bool),
    reset_rgb: unsafe extern "C" fn() -> bool,
    close: unsafe extern "C" fn(),
}

unsafe fn load_fn<T: Copy>(lib: &Library, name: &[u8]) -> Result<T, String> {
    let s: Symbol<T> = lib.get(name).map_err(|e| e.to_string())?;
    Ok(*s)
}

impl RgbSdk {
    pub fn open(dylib_path: &Path) -> Result<Self, String> {
        let lib = unsafe { Library::new(dylib_path) }
            .map_err(|e| format!("dlopen failed: {e}"))?;
        let kbd_connected = unsafe { load_fn(&lib, b"wooting_rgb_kbd_connected")? };
        let array_set_single = unsafe { load_fn(&lib, b"wooting_rgb_array_set_single")? };
        let array_update_keyboard = unsafe { load_fn(&lib, b"wooting_rgb_array_update_keyboard")? };
        let array_auto_update = unsafe { load_fn(&lib, b"wooting_rgb_array_auto_update")? };
        let reset_rgb = unsafe { load_fn(&lib, b"wooting_rgb_reset_rgb")? };
        let close = unsafe { load_fn(&lib, b"wooting_rgb_close")? };
        Ok(Self {
            _lib: lib,
            kbd_connected,
            array_set_single,
            array_update_keyboard,
            array_auto_update,
            reset_rgb,
            close,
        })
    }

    pub fn connected(&self) -> bool {
        unsafe { (self.kbd_connected)() }
    }

    pub fn set_auto_update(&self, on: bool) {
        unsafe { (self.array_auto_update)(on) }
    }

    pub fn set_single(&self, row: u8, column: u8, r: u8, g: u8, b: u8) -> bool {
        unsafe { (self.array_set_single)(row, column, r, g, b) }
    }

    pub fn update_keyboard(&self) -> bool {
        unsafe { (self.array_update_keyboard)() }
    }

    pub fn reset(&self) -> bool {
        unsafe { (self.reset_rgb)() }
    }

    pub fn close(&self) {
        unsafe { (self.close)() }
    }
}

unsafe impl Send for RgbSdk {}
unsafe impl Sync for RgbSdk {}
