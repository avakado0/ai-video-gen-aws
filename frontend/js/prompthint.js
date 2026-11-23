/** Prompt tips: the manifest's optional `prompt_hint` and the user's show/hide choice. Pure functions, testable with `node --test`. */

export const STORAGE_KEY = 'promptTipsShown';

/** True when the manifest carries tips and has a prompt field to put them under. */
export function hasHint(manifest) {
  return Array.isArray(manifest?.prompt_hint) && manifest.prompt_hint.length > 0 && manifest.prompt !== 'none';
}

/** Whether the user wants the tips shown. Default yes; a missing, odd or unreadable stored value counts as yes. */
export function tipsShown(storage) {
  try {
    return storage?.getItem(STORAGE_KEY) !== 'false';
  } catch {
    return true;
  }
}

/** Remember the choice. Storage can be blocked (private mode): then the choice lasts until the page reloads. */
export function setTipsShown(storage, shown) {
  try {
    storage?.setItem(STORAGE_KEY, String(Boolean(shown)));
  } catch {
    /* ignore */
  }
}
