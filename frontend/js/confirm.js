/** "Are you sure?" step before a job is submitted: shows the prompt and the settings as they will be sent. */

const label = (name) => name.replace(/_/g, ' ');

const show = (value) => {
  if (value === null || value === undefined || value === '') return 'not set';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
};

const row = (term, desc) => {
  const dt = Object.assign(document.createElement('dt'), { textContent: term });
  const dd = Object.assign(document.createElement('dd'), { textContent: desc });
  return [dt, dd];
};

/**
 * The rows the dialog lists, in manifest order. Pure, so it needs no DOM.
 * @param {import('./api/ApiClient.js').Manifest} manifest
 * @param {{prompt: string, files: Object<string, File[]>, params: object}} values from ManifestForm.values()
 * @returns {Array<[string, string]>} [term, description]
 */
export function summaryRows(manifest, values) {
  const rows = [];
  if ((manifest.prompt ?? 'required') !== 'none') rows.push(['Prompt', values.prompt || 'none']);
  for (const slot of manifest.inputs) {
    const files = values.files[slot.name] ?? [];
    rows.push([slot.label, files.length ? files.map((f) => f.name).join(', ') : 'none']);
  }
  for (const p of manifest.params ?? []) rows.push([label(p.name), show(values.params[p.name])]);
  return rows;
}

/**
 * Open the dialog and resolve true on "Generate", false on "Edit" or Escape.
 * @param {HTMLDialogElement} dialog
 */
export function confirmJob(dialog, manifest, values) {
  const list = dialog.querySelector('dl');
  list.replaceChildren(...summaryRows(manifest, values).flatMap(([term, desc]) => row(term, desc)));
  return new Promise((resolve) => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'ok'), { once: true });
    dialog.returnValue = '';
    dialog.showModal();
  });
}
