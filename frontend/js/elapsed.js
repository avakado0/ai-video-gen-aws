/** Elapsed-time text for pending jobs. Pure functions, so they can be tested with `node --test`. */

/** "42 s" under a minute, "3 min" after; '' when the date is missing or unparseable. */
export function formatElapsed(created, now = Date.now()) {
  const start = Date.parse(created);
  if (Number.isNaN(start)) return '';
  const seconds = Math.max(0, Math.floor((now - start) / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min`;
}

/** The line under a pending job: time waited so far, plus the cold-start warning. */
export function pendingNote(created, now = Date.now()) {
  const elapsed = formatElapsed(created, now);
  const warning = 'After an idle period the first clip can take up to 20 minutes to start, while a GPU machine boots.';
  return elapsed ? `Waiting ${elapsed}. ${warning}` : warning;
}
