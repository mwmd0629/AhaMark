export type Region = { x: number; y: number; width: number; height: number };

// Stored coordinates always describe the unrotated original page. These helpers
// only transform the editor overlay for display and are exact inverses.
export function originalToDisplay(
  region: Region,
  rotation: 0 | 90 | 180 | 270,
): Region {
  const { x, y, width, height } = region;
  if (rotation === 90)
    return { x: 1 - y - height, y: x, width: height, height: width };
  if (rotation === 180)
    return { x: 1 - x - width, y: 1 - y - height, width, height };
  if (rotation === 270)
    return { x: y, y: 1 - x - width, width: height, height: width };
  return region;
}

export function displayToOriginal(
  region: Region,
  rotation: 0 | 90 | 180 | 270,
): Region {
  if (rotation === 90) return originalToDisplay(region, 270);
  if (rotation === 270) return originalToDisplay(region, 90);
  return originalToDisplay(region, rotation);
}
