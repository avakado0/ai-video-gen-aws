/** Cost text for settled jobs (plan 08). Pure functions, so they can be tested with `node --test`.
 *
 * The number is an estimate of the GPU instance alone (all attempts, at the Spot price): no boot or
 * idle share, no download traffic. The label says so. */

/** "~$0.07"; finer below a cent; '' when the cost is unknown. */
export function formatCost(usd) {
  if (typeof usd !== 'number' || !Number.isFinite(usd) || usd < 0) return '';
  if (usd < 0.001) return '<$0.001';
  return `~$${usd.toFixed(usd < 0.01 ? 3 : 2)}`;
}

/** The line under a ready or failed job: "~$0.07 · 2 tries · GPU only"; '' when nothing is known. */
export function costNote(job) {
  if (job.status === 'pending') return '';
  const cost = formatCost(job.cost_usd);
  const tries = Number.isInteger(job.attempts) && job.attempts > 0
    ? `${job.attempts} ${job.attempts === 1 ? 'try' : 'tries'}` : '';
  return [cost, tries, cost && 'GPU only'].filter(Boolean).join(' · ');
}
