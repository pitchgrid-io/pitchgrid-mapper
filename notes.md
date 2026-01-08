A universal microtonal link between isomorphic controllers and PitchGrid


### To VST or not to VST

Should connect directly to the controller for usabiltity, might not play well with DAW recordings, since for direct midi the track's piano roll is bypassed.

So if VST, there is added complexity in the setup, controller must be chosen both in DAW and Plugin, settings should enable bidirectional comms for setting colors and configuring notes. 

So No VST, but a separate executable.

# Standalone features

- Can record in piano roll according to PitchGrid mapping.
- Auto connect to controller from companion (no controller -> computer keyboard)
- Select Companion's MIDI port in DAW, hook up to PitchGrid
- Controller may have unmapped MIDI notes, which it can highlight, giving the user transparency, but also need for finding a good configuration.
- Save companion preset with controller+layout
- Allow virtual play, i.e. without controller attached
	- **Computer keyboard as virtual MIDI input device** - when computer keyboard layout selected and app in foreground, typing sends MIDI messages
	- Optional: computer keyboard overlay in case it's virtual
- Optional: unmapped notes via OSC from companion to PitchGrid.
- **Highlight currently playing notes in UI (ALWAYS, regardless of layout)** - visual feedback for which pads are pressed
- Highlight piano-roll notes by receiving OSC playing note coordinates from PitchGrid.
-

## Layouts
- Isomorphic
	- Operations: select root pad, skew (along 2 or 3 axes depending on quad/hex), rotate left/right (transform depends on quad or hex) , flip along 2 (quad) or 3 (hex) axes, move in one of 4 (quad) or 6 (hex) directions.
- String-like
	- Operations: string orientation (2 or 3 different choices, depending on quad/hex), select root pad, row offset, move in one of 4 (quad) or 6 (hex) directions.
- Piano-like-unfolded (scale degrees on one line, preserving accidental direction)
	- Operations: strip orientation (2 or 3 different choices, depending on quad/hex),  strip width, set root pad (constrained), move along 2 strip directions, define accidental direction (2 for hex and(!) for quad), strips offset, scale line position within strips
- Piano-like-folded (strip width=2)
	- Operations: strip orientation (2 or 3 different choices, depending on quad/hex), set root pad (constrained), move along 2 strip directions, define accidental direction (2 for hex and(!) for quad), strips offset
- EDO-iso
	- Same as isomorphic but with multiple pad to note assignments based on EDO cycle
## Coloring Scheme

- for isomorphic
	- fixed for different roles
		- root
		- on-scale
		- on-superscale 
		- other
		- all of the above in mapped/unmapped variants
	- Circular HLC/HLS according to scale degree (only available if controller supports RGB)
		- root brightest
		- on-scale bright
		- off-scale dark(?)
		- unmapped off(?) 
	- Harmony based HLC/HLS (only available if controller supports RGB)
		- root white
		- Look at sensory dissonance curve. brightness = sensory consonance
		- different limits 3,5,7,9 in different colors
		- opposite color for inverse (i.e. P5 has opposite color from P4)
		- This will be killer for finding cool tunings.


## Stack
- ~~Nim+Tauri+Svelte(?)~~
- **Decision: Python 3.12 + FastAPI + Svelte** (implemented)







