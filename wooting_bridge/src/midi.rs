use smallvec::SmallVec;

/// Compact representation of an outgoing MIDI message destined for the host MIDI handler.
/// Stored as a 1-3 byte payload; SmallVec batches inline up to 8.
pub type MidiBatch = SmallVec<[MidiBytes; 8]>;
pub type MidiBytes = SmallVec<[u8; 4]>;

#[inline]
pub fn note_on(channel: u8, note: u8, velocity: u8) -> MidiBytes {
    let mut v = SmallVec::new();
    v.push(0x90 | (channel & 0x0F));
    v.push(note & 0x7F);
    v.push(velocity & 0x7F);
    v
}

#[inline]
pub fn note_off(channel: u8, note: u8) -> MidiBytes {
    let mut v = SmallVec::new();
    v.push(0x80 | (channel & 0x0F));
    v.push(note & 0x7F);
    v.push(0);
    v
}

#[inline]
pub fn control_change(channel: u8, controller: u8, value: u8) -> MidiBytes {
    let mut v = SmallVec::new();
    v.push(0xB0 | (channel & 0x0F));
    v.push(controller & 0x7F);
    v.push(value & 0x7F);
    v
}

#[inline]
pub fn channel_pressure(channel: u8, value: u8) -> MidiBytes {
    let mut v = SmallVec::new();
    v.push(0xD0 | (channel & 0x0F));
    v.push(value & 0x7F);
    v
}

#[inline]
pub fn pitch_bend_center(channel: u8) -> MidiBytes {
    let mut v = SmallVec::new();
    v.push(0xE0 | (channel & 0x0F));
    v.push(0x00);
    v.push(0x40);
    v
}

/// MPE Configuration Message (RPN 6) on global channel 1, member channels 2..16 (zone size 15).
/// Sequence: B0 64 06  B0 65 00  B0 06 0F
pub fn mpe_configuration_zone(member_channel_count: u8) -> MidiBatch {
    let mut batch = SmallVec::new();
    batch.push(control_change(0, 0x64, 0x06));
    batch.push(control_change(0, 0x65, 0x00));
    batch.push(control_change(0, 0x06, member_channel_count.min(15)));
    batch
}
