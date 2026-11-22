/** Start-image size rule. Pure functions, so they can be tested with `node --test`.
 *  The worker checks the same minimum (worker/wan22.py MIN_IMAGE_SIDE); checking here saves the
 *  wait for a GPU instance to boot just to be told the photo is too small. */

/** A message when the shorter side is under `minSide` pixels, else null (also null without a limit). */
export function shortSideProblem(name, width, height, minSide) {
  if (!minSide) return null;
  const shorter = Math.min(width, height);
  if (shorter >= minSide) return null;
  return `${name}: too small (${width}x${height}). Its shorter side must be at least ${minSide} pixels.`;
}

/** The hint under the file picker. */
export function minSideHint(minSide) {
  return minSide ? `at least ${minSide} px on the shorter side` : '';
}
