/// MPE member channel allocator.
///
/// Allocates from `[low..=high]` (typically 2..=16 for MPE). Tracks per-channel
/// release timestamps to bias new allocations toward channels that have been
/// idle the longest. On overflow (no idle channels), steals the oldest active
/// allocation by returning its identity to the caller — the caller is
/// responsible for emitting the corresponding NoteOff.

use std::time::Instant;

#[derive(Debug, Clone, Copy)]
struct Slot {
    note: Option<u8>, // controller note this channel is currently holding
    keycode: Option<u16>,
    released_at: Option<Instant>,
}

#[derive(Debug)]
pub struct ChannelAllocator {
    low: u8,
    high: u8,
    slots: Vec<Slot>,
}

#[derive(Debug)]
pub struct StolenAllocation {
    pub channel: u8,
    pub note: u8,
    pub keycode: u16,
}

impl ChannelAllocator {
    pub fn new(low: u8, high: u8) -> Self {
        let n = (high - low + 1) as usize;
        Self {
            low,
            high,
            slots: vec![
                Slot {
                    note: None,
                    keycode: None,
                    released_at: None,
                };
                n
            ],
        }
    }

    /// Acquire a channel for (keycode, note). Returns the chosen channel and,
    /// if the allocator had to steal, the (channel, note, keycode) of the
    /// allocation that was forcibly evicted so the caller can emit a NoteOff.
    pub fn acquire(&mut self, keycode: u16, note: u8) -> (u8, Option<StolenAllocation>) {
        // Prefer free slots: pick the one with the oldest `released_at` (LRU).
        let mut best_free: Option<(usize, Instant)> = None;
        let mut never_used: Option<usize> = None;
        for (i, s) in self.slots.iter().enumerate() {
            if s.note.is_none() {
                if let Some(t) = s.released_at {
                    match best_free {
                        Some((_, bt)) if bt <= t => {}
                        _ => best_free = Some((i, t)),
                    }
                } else if never_used.is_none() {
                    never_used = Some(i);
                }
            }
        }
        let chosen = best_free.map(|(i, _)| i).or(never_used);
        if let Some(i) = chosen {
            self.slots[i] = Slot {
                note: Some(note),
                keycode: Some(keycode),
                released_at: None,
            };
            return (self.low + i as u8, None);
        }
        // Steal the oldest active. We don't track per-allocation start time, so
        // approximate "oldest" by lowest released_at (none means freshest).
        // Fallback: just steal index 0.
        let steal_idx = self
            .slots
            .iter()
            .enumerate()
            .min_by_key(|(_, s)| s.released_at.unwrap_or_else(Instant::now))
            .map(|(i, _)| i)
            .unwrap_or(0);
        let evicted = self.slots[steal_idx];
        self.slots[steal_idx] = Slot {
            note: Some(note),
            keycode: Some(keycode),
            released_at: None,
        };
        (
            self.low + steal_idx as u8,
            evicted
                .note
                .zip(evicted.keycode)
                .map(|(n, k)| StolenAllocation {
                    channel: self.low + steal_idx as u8,
                    note: n,
                    keycode: k,
                }),
        )
    }

    /// Release the channel currently holding `keycode`. Returns the (channel, note)
    /// pair that was released, or None if the keycode was not active.
    pub fn release(&mut self, keycode: u16, now: Instant) -> Option<(u8, u8)> {
        for (i, s) in self.slots.iter_mut().enumerate() {
            if s.keycode == Some(keycode) {
                let note = s.note.take().unwrap_or(0);
                s.keycode = None;
                s.released_at = Some(now);
                return Some((self.low + i as u8, note));
            }
        }
        None
    }

    pub fn channel_for_keycode(&self, keycode: u16) -> Option<u8> {
        for (i, s) in self.slots.iter().enumerate() {
            if s.keycode == Some(keycode) {
                return Some(self.low + i as u8);
            }
        }
        None
    }

    pub fn active_count(&self) -> usize {
        self.slots.iter().filter(|s| s.note.is_some()).count()
    }

    pub fn capacity(&self) -> usize {
        self.slots.len()
    }

    pub fn iter_active(&self) -> impl Iterator<Item = (u8, u8, u16)> + '_ {
        self.slots.iter().enumerate().filter_map(move |(i, s)| {
            match (s.note, s.keycode) {
                (Some(n), Some(k)) => Some((self.low + i as u8, n, k)),
                _ => None,
            }
        })
    }

    pub fn _force_release_all(&mut self, now: Instant) -> Vec<(u8, u8)> {
        let mut out = Vec::new();
        for (i, s) in self.slots.iter_mut().enumerate() {
            if let Some(n) = s.note.take() {
                s.keycode = None;
                s.released_at = Some(now);
                out.push((self.low + i as u8, n));
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn acquires_unique_channels_until_capacity() {
        let mut a = ChannelAllocator::new(2, 16);
        assert_eq!(a.capacity(), 15);
        let mut seen = Vec::new();
        for k in 0..15u16 {
            let (ch, stolen) = a.acquire(k, 60 + k as u8);
            assert!(stolen.is_none());
            seen.push(ch);
        }
        seen.sort();
        seen.dedup();
        assert_eq!(seen.len(), 15);
    }

    #[test]
    fn overflow_steals_allocation() {
        let mut a = ChannelAllocator::new(2, 4); // 3 channels
        a.acquire(1, 60);
        a.acquire(2, 61);
        a.acquire(3, 62);
        let (_ch, stolen) = a.acquire(4, 63);
        assert!(stolen.is_some(), "expected steal on overflow");
    }

    #[test]
    fn release_marks_channel_free() {
        let mut a = ChannelAllocator::new(2, 4);
        let (ch1, _) = a.acquire(1, 60);
        a.release(1, Instant::now());
        let (ch2, _) = a.acquire(2, 61);
        // After release, the next allocation should reuse the freed channel
        // (it's the only one with released_at set, hence "oldest free").
        // ch1 was used and released, ch2 should be different (never_used has priority).
        // Actually our LRU prefers slots with released_at over never_used, so
        // ch2 should equal ch1.
        assert_eq!(ch1, ch2, "freed channel should be reused");
    }

    #[test]
    fn channel_for_keycode_lookup() {
        let mut a = ChannelAllocator::new(2, 16);
        let (ch, _) = a.acquire(0x1d, 60);
        assert_eq!(a.channel_for_keycode(0x1d), Some(ch));
        assert_eq!(a.channel_for_keycode(0x1e), None);
    }
}
