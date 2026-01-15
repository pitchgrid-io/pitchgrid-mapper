<script lang="ts">
  /**
   * Transform icon component that generates angle-aware SVG icons
   * for shift, skew, and reflect operations.
   *
   * The icons respect the controller's physical geometry:
   * - HorizonToRowAngle: angle of device X axis from horizontal
   * - RowToColAngle: angle between device X and Y axes
   */
  export let type: 'shift' | 'skew' | 'reflect' | 'rotate';
  export let direction: 'left' | 'right' | 'up' | 'down' | 'upright' | 'downleft' | 'upleft' | 'downright' | 'x_hex' | 'y_hex' | 'xy_hex' | 'left_hex' | 'right_hex';
  export let horizonToRowAngle: number = 0; // Angle in degrees
  export let rowToColAngle: number = 90; // Angle in degrees
  export let size: number = 24;

  // Calculate actual angles for the three main directions in device coordinates
  // Device X axis points at horizonToRowAngle from horizontal
  const deviceXAngle = horizonToRowAngle;
  // Device Y axis points at horizonToRowAngle + rowToColAngle from horizontal
  const deviceYAngle = horizonToRowAngle + rowToColAngle;
  // Device (-1, 1) direction (up-left in hex grids) - note the calculation for (-1,1) in device coords
  // This is the direction perpendicular to X in the hex lattice
  const deviceDiagAngle = horizonToRowAngle + 180 - rowToColAngle;

  function getShiftIcon(): string {
    let angle = 0;

    switch (direction) {
      case 'left':
        angle = deviceXAngle + 180; // -X direction
        break;
      case 'right':
        angle = deviceXAngle; // +X direction
        break;
      case 'up':
        angle = deviceYAngle; // +Y direction
        break;
      case 'down':
        angle = deviceYAngle + 180; // -Y direction
        break;
      case 'upright':
        angle = deviceYAngle; // +Y direction
        break;
      case 'downleft':
        angle = deviceYAngle + 180; // -Y direction
        break;
      case 'upleft':
        angle = deviceDiagAngle; // (-1,1) direction
        break;
      case 'downright':
        angle = deviceDiagAngle + 180; // (1,-1) direction
        break;
    }

    // Normalize angle
    angle = angle % 360;
    if (angle < 0) angle += 360;

    // Create arrow extending in both directions from center
    const cx = size / 2;
    const cy = size / 2;
    const halfLength = size * 0.4;
    const headSize = size * 0.18;

    // Calculate arrow endpoints (extending both ways)
    const rad = (angle * Math.PI) / 180;
    const ex = cx + halfLength * Math.cos(rad);
    const ey = cy - halfLength * Math.sin(rad); // SVG Y is inverted
    const sx = cx - halfLength * Math.cos(rad);
    const sy = cy + halfLength * Math.sin(rad);

    // Calculate arrowhead points at the forward end
    const headAngle1 = rad + (5 * Math.PI) / 6;
    const headAngle2 = rad - (5 * Math.PI) / 6;
    const hx1 = ex + headSize * Math.cos(headAngle1);
    const hy1 = ey - headSize * Math.sin(headAngle1);
    const hx2 = ex + headSize * Math.cos(headAngle2);
    const hy2 = ey - headSize * Math.sin(headAngle2);

    return `
      <line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
      <path d="M ${hx1} ${hy1} L ${ex} ${ey} L ${hx2} ${hy2}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />
    `;
  }

  function getSkewIcon(): string {
    let angle = 0;
    let flipHorizontal = false;

    switch (direction) {
      case 'left':
        angle = deviceXAngle + 180;
        flipHorizontal = true;
        break;
      case 'right':
        angle = deviceXAngle;
        flipHorizontal = false;
        break;
      case 'upright':
        angle = deviceYAngle;
        flipHorizontal = false;
        break;
      case 'downleft':
        angle = deviceYAngle + 180;
        flipHorizontal = true;
        break;
      case 'upleft':
        // Swapped with downright
        angle = deviceDiagAngle + 180;
        flipHorizontal = false;
        break;
      case 'downright':
        // Swapped with upleft
        angle = deviceDiagAngle;
        flipHorizontal = true;
        break;
    }

    // Normalize angle
    angle = angle % 360;
    if (angle < 0) angle += 360;

    const cx = size / 2;
    const cy = size / 2;
    const lineLength = size * 1.0; // Longer line
    const arrowLength = size * 0.6; // Longer arrows like shift
    const arrowHead = size * 0.18;
    const arrowOffset = size * 0.25; // Displacement from line

    // Draw line along the skew direction (this is the fixed axis)
    const rad = (angle * Math.PI) / 180;
    const perpRad = rad + Math.PI / 2;
    const lx1 = cx - (lineLength / 2) * Math.cos(rad);
    const ly1 = cy + (lineLength / 2) * Math.sin(rad);
    const lx2 = cx + (lineLength / 2) * Math.cos(rad);
    const ly2 = cy - (lineLength / 2) * Math.sin(rad);

    // Arrow positions: displaced perpendicular to the line
    // Upper arrow (pointing right in prototype, then rotated)
    const upperCenterX = cx + arrowOffset * Math.cos(perpRad);
    const upperCenterY = cy - arrowOffset * Math.sin(perpRad);
    const a1sx = upperCenterX - (arrowLength / 2) * Math.cos(rad);
    const a1sy = upperCenterY + (arrowLength / 2) * Math.sin(rad);
    const a1ex = upperCenterX + (arrowLength / 2) * Math.cos(rad);
    const a1ey = upperCenterY - (arrowLength / 2) * Math.sin(rad);

    // Arrowhead for upper arrow - attach to correct end based on flip
    const a1HeadX = flipHorizontal ? a1sx : a1ex;
    const a1HeadY = flipHorizontal ? a1sy : a1ey;
    const a1HeadAngle = flipHorizontal ? rad + Math.PI : rad;
    const a1h1x = a1HeadX + arrowHead * Math.cos(a1HeadAngle + (5 * Math.PI) / 6);
    const a1h1y = a1HeadY - arrowHead * Math.sin(a1HeadAngle + (5 * Math.PI) / 6);
    const a1h2x = a1HeadX + arrowHead * Math.cos(a1HeadAngle - (5 * Math.PI) / 6);
    const a1h2y = a1HeadY - arrowHead * Math.sin(a1HeadAngle - (5 * Math.PI) / 6);

    // Lower arrow (pointing left in prototype, then rotated)
    const lowerCenterX = cx - arrowOffset * Math.cos(perpRad);
    const lowerCenterY = cy + arrowOffset * Math.sin(perpRad);
    const a2sx = lowerCenterX + (arrowLength / 2) * Math.cos(rad);
    const a2sy = lowerCenterY - (arrowLength / 2) * Math.sin(rad);
    const a2ex = lowerCenterX - (arrowLength / 2) * Math.cos(rad);
    const a2ey = lowerCenterY + (arrowLength / 2) * Math.sin(rad);

    // Arrowhead for lower arrow - attach to opposite end from upper arrow
    const a2HeadX = flipHorizontal ? a2sx : a2ex;
    const a2HeadY = flipHorizontal ? a2sy : a2ey;
    const a2HeadAngle = flipHorizontal ? rad : rad + Math.PI;
    const a2h1x = a2HeadX + arrowHead * Math.cos(a2HeadAngle + (5 * Math.PI) / 6);
    const a2h1y = a2HeadY - arrowHead * Math.sin(a2HeadAngle + (5 * Math.PI) / 6);
    const a2h2x = a2HeadX + arrowHead * Math.cos(a2HeadAngle - (5 * Math.PI) / 6);
    const a2h2y = a2HeadY - arrowHead * Math.sin(a2HeadAngle - (5 * Math.PI) / 6);

    return `
      <line x1="${lx1}" y1="${ly1}" x2="${lx2}" y2="${ly2}" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <line x1="${a1sx}" y1="${a1sy}" x2="${a1ex}" y2="${a1ey}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
      <path d="M ${a1h1x} ${a1h1y} L ${a1HeadX} ${a1HeadY} L ${a1h2x} ${a1h2y}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />
      <line x1="${a2sx}" y1="${a2sy}" x2="${a2ex}" y2="${a2ey}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
      <path d="M ${a2h1x} ${a2h1y} L ${a2HeadX} ${a2HeadY} L ${a2h2x} ${a2h2y}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />
    `;
  }

  function getReflectIcon(): string {
    let angle = 0;

    switch (direction) {
      case 'left':
      case 'right':
        // Reflect on vertical axis (perpendicular to X)
        angle = deviceXAngle;
        break;
      case 'up':
      case 'down':
        // Reflect on horizontal axis (perpendicular to Y)
        angle = deviceYAngle;
        break;
      case 'x_hex':
        // Reflect on axis perpendicular to device X
        angle = deviceXAngle;
        break;
      case 'y_hex':
        // Reflect on axis perpendicular to device Y
        angle = deviceYAngle;
        break;
      case 'xy_hex':
        // Reflect on axis perpendicular to diagonal
        angle = deviceDiagAngle;
        break;
    }

    // Normalize angle
    angle = angle % 360;
    if (angle < 0) angle += 360;

    const cx = size / 2;
    const cy = size / 2;
    const lineLength = size * 1.0; // Longer line
    const arrowLength = size * 0.85; // Much longer arrows
    const arrowHead = size * 0.18;

    // Draw reflection axis line
    const rad = (angle * Math.PI) / 180;
    const lx1 = cx - (lineLength / 2) * Math.cos(rad);
    const ly1 = cy + (lineLength / 2) * Math.sin(rad);
    const lx2 = cx + (lineLength / 2) * Math.cos(rad);
    const ly2 = cy - (lineLength / 2) * Math.sin(rad);

    // Draw double-headed arrow perpendicular to the axis
    const perpRad = rad + Math.PI / 2;
    const ax1 = cx - (arrowLength / 2) * Math.cos(perpRad);
    const ay1 = cy + (arrowLength / 2) * Math.sin(perpRad);
    const ax2 = cx + (arrowLength / 2) * Math.cos(perpRad);
    const ay2 = cy - (arrowLength / 2) * Math.sin(perpRad);

    // Arrowhead 1 (pointing away from center)
    const h1a1x = ax1 - arrowHead * Math.cos(perpRad + (5 * Math.PI) / 6);
    const h1a1y = ay1 + arrowHead * Math.sin(perpRad + (5 * Math.PI) / 6);
    const h1a2x = ax1 - arrowHead * Math.cos(perpRad - (5 * Math.PI) / 6);
    const h1a2y = ay1 + arrowHead * Math.sin(perpRad - (5 * Math.PI) / 6);

    // Arrowhead 2 (pointing away from center)
    const h2a1x = ax2 + arrowHead * Math.cos(perpRad + (5 * Math.PI) / 6);
    const h2a1y = ay2 - arrowHead * Math.sin(perpRad + (5 * Math.PI) / 6);
    const h2a2x = ax2 + arrowHead * Math.cos(perpRad - (5 * Math.PI) / 6);
    const h2a2y = ay2 - arrowHead * Math.sin(perpRad - (5 * Math.PI) / 6);

    return `
      <line x1="${lx1}" y1="${ly1}" x2="${lx2}" y2="${ly2}" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-dasharray="3,2" />
      <line x1="${ax1}" y1="${ay1}" x2="${ax2}" y2="${ay2}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
      <path d="M ${h1a1x} ${h1a1y} L ${ax1} ${ay1} L ${h1a2x} ${h1a2y}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />
      <path d="M ${h2a1x} ${h2a1y} L ${ax2} ${ay2} L ${h2a2x} ${h2a2y}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />
    `;
  }

  function getRotateIcon(): string {
    const isLeft = (direction === 'left_hex' || direction === 'left');

    // Position the angle corner at lower left
    const cornerX = size * 0.05;
    const cornerY = size * 0.95;
    const lineLength = size * 1.0;

    // First line at HorizonToRowAngle
    const angle1 = horizonToRowAngle;
    const rad1 = (angle1 * Math.PI) / 180;
    const x1 = cornerX + lineLength * Math.cos(rad1);
    const y1 = cornerY - lineLength * Math.sin(rad1);

    // Second line at HorizonToRowAngle + RowToColAngle
    const angle2 = horizonToRowAngle + rowToColAngle;
    const rad2 = (angle2 * Math.PI) / 180;
    const x2 = cornerX + lineLength * Math.cos(rad2);
    const y2 = cornerY - lineLength * Math.sin(rad2);

    // Arc centered at corner intersection, connecting the two lines
    const arcRadius = size * 0.75;

    // Arc goes from angle1 to angle2 (counterclockwise sweep)
    const arrowInsetRad = 5.0 * (Math.PI / 180); // 5 degrees in radians
    const arcStartX = cornerX + arcRadius * Math.cos(rad1 + arrowInsetRad);
    const arcStartY = cornerY - arcRadius * Math.sin(rad1 + arrowInsetRad);
    const arcEndX = cornerX + arcRadius * Math.cos(rad2 - arrowInsetRad);
    const arcEndY = cornerY - arcRadius * Math.sin(rad2 - arrowInsetRad);

    // Calculate angle difference to determine large arc flag
    let angleDiff = angle2 - angle1;
    while (angleDiff < 0) angleDiff += 360;
    while (angleDiff >= 360) angleDiff -= 360;

    // Arrowhead placement depends on direction
    const arrowSize = size * 0.18;
    let arrowX, arrowY, arrowAngle;

    if (isLeft) {
      // Rotate left: arrowhead at end (top) of arc, pointing counterclockwise
      arrowX = arcEndX;
      arrowY = arcEndY;
      arrowAngle = rad2 + 0.8 * Math.PI / 2; // Perpendicular to end radius (counterclockwise)
    } else {
      // Rotate right: arrowhead at start (bottom) of arc, pointing clockwise
      arrowX = arcStartX;
      arrowY = arcStartY;
      arrowAngle = rad1 - 0.8 * Math.PI / 2; // Perpendicular to start radius (clockwise)
    }

    const ah1x = arrowX + arrowSize * Math.cos(arrowAngle + (5 * Math.PI) / 6);
    const ah1y = arrowY - arrowSize * Math.sin(arrowAngle + (5 * Math.PI) / 6);
    const ah2x = arrowX + arrowSize * Math.cos(arrowAngle - (5 * Math.PI) / 6);
    const ah2y = arrowY - arrowSize * Math.sin(arrowAngle - (5 * Math.PI) / 6);

    return `
      <line x1="${cornerX}" y1="${cornerY}" x2="${x1}" y2="${y1}" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <line x1="${cornerX}" y1="${cornerY}" x2="${x2}" y2="${y2}" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
      <path d="M ${arcStartX} ${arcStartY} A ${arcRadius} ${arcRadius} 0 0 0 ${arcEndX} ${arcEndY}"
            stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" />
      <path d="M ${ah1x} ${ah1y} L ${arrowX} ${arrowY} L ${ah2x} ${ah2y}"
            stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />
    `;
  }

  $: iconPath = type === 'shift' ? getShiftIcon()
              : type === 'skew' ? getSkewIcon()
              : type === 'reflect' ? getReflectIcon()
              : type === 'rotate' ? getRotateIcon()
              : '';
</script>

<svg width={size} height={size} viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">
  {@html iconPath}
</svg>
