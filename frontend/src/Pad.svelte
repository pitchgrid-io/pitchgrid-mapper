<script lang="ts">
  export let x: number;  // logical x
  export let y: number;  // logical y
  export let phys_x: number;  // physical x (center)
  export let phys_y: number;  // physical y (center)
  export let shape: Array<[number, number]> = [];  // Voronoi vertices in physical coordinates
  export let isActive: boolean = false;
  export let color: string = '#3a3a3a';
  export let onNoteOn: () => void = () => {};
  export let onNoteOff: () => void = () => {};
  export let midiNote: number | null | undefined = undefined;
  export let mosCoord: [number, number] | null | undefined = undefined;
  export let mosLabelDigit: string | null | undefined = undefined;
  export let mosLabelLetter: string | null | undefined = undefined;
  export let labelType: 'digits' | 'letters' | 'mos_coords' | 'device_coords' | 'midi_note' = 'digits';

  // Compute label to display based on labelType
  // Use nullish coalescing (??) to handle both null and undefined from JSON
  $: displayLabel = (() => {
    // For non-coordinate label types, show blank if data is missing (unmapped pad)
    if (labelType === 'digits') return mosLabelDigit ?? '';
    if (labelType === 'letters') return mosLabelLetter ?? '';
    if (labelType === 'midi_note') return midiNote != null ? String(midiNote) : '';
    if (labelType === 'mos_coords') return mosCoord != null ? `${mosCoord[0]},${mosCoord[1]}` : '';
    // Device coordinates are always available
    return `${x},${y}`;
  })();

  // Generate SVG path from Voronoi shape
  $: shapePath = shape && shape.length > 0
    ? generatePath(shape, phys_x, phys_y)
    : generateHexagonPath(phys_x, phys_y, 0.5);

  // Calculate pad height from shape vertices for font sizing
  $: padHeight = shape && shape.length > 0
    ? Math.max(...shape.map(v => v[1])) - Math.min(...shape.map(v => v[1]))
    : 1.0;
  $: fontSize = padHeight * 0.25;

  function generatePath(vertices: Array<[number, number]>, centerX: number, centerY: number): string {
    if (vertices.length === 0) return '';

    let path = '';
    vertices.forEach((vertex, i) => {
      const vx = vertex[0];
      const vy = vertex[1];

      if (i === 0) {
        path += `M ${vx} ${vy}`;
      } else {
        path += ` L ${vx} ${vy}`;
      }
    });
    path += ' Z';
    return path;
  }

  function generateHexagonPath(centerX: number, centerY: number, radius: number): string {
    let path = '';

    for (let i = 0; i < 6; i++) {
      const angle = (Math.PI / 3) * i - Math.PI / 2;
      const vx = centerX + radius * Math.cos(angle);
      const vy = centerY + radius * Math.sin(angle);

      if (i === 0) {
        path += `M ${vx} ${vy}`;
      } else {
        path += ` L ${vx} ${vy}`;
      }
    }
    path += ' Z';
    return path;
  }

  function handleMouseDown(event: MouseEvent) {
    event.preventDefault();
    onNoteOn();
  }

  function handleMouseUp(event: MouseEvent) {
    event.preventDefault();
    onNoteOff();
  }

  function handleMouseLeave(event: MouseEvent) {
    // Send note-off when mouse leaves the pad while pressed
    if (event.buttons > 0) {
      onNoteOff();
    }
  }
</script>

<!-- svelte-ignore a11y-click-events-have-key-events -->
<!-- svelte-ignore a11y-no-static-element-interactions -->
<g
  on:mousedown={handleMouseDown}
  on:mouseup={handleMouseUp}
  on:mouseleave={handleMouseLeave}
  class="pad"
  class:active={isActive}
>
  <path
    d={shapePath}
    fill={isActive ? '#ff4444' : color}
    stroke={isActive ? '#ff6666' : '#555555'}
    stroke-width="0.05"
  />
  <text
    x={phys_x}
    y={phys_y}
    text-anchor="middle"
    dominant-baseline="middle"
    font-family="monospace"
    font-size={fontSize}
    fill="#fff"
  >
    {displayLabel}
  </text>
</g>

<style>
  .pad {
    cursor: pointer;
  }

  .pad:hover path {
    stroke: #6ee0d4;
    stroke-width: 0.08;
  }
</style>
