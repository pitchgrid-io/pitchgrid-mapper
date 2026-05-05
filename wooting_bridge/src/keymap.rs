use std::collections::HashMap;

use crate::profiles::KeyDescriptor;

#[derive(Debug, Clone, Default)]
pub struct Keymap {
    /// HID keycode -> KeyDescriptor (logical x, y, controller MIDI note).
    pub map: HashMap<u16, KeyDescriptor>,
    /// (logical_x, logical_y) -> hardware (row, column) for RGB addressing.
    pub rgb_addr: HashMap<(i16, i16), (u8, u8)>,
}

impl Keymap {
    pub fn lookup(&self, keycode: u16) -> Option<KeyDescriptor> {
        self.map.get(&keycode).copied()
    }

    pub fn rgb_for_logical(&self, x: i16, y: i16) -> Option<(u8, u8)> {
        self.rgb_addr.get(&(x, y)).copied()
    }
}
