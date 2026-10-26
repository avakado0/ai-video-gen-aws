/** Renders the job form from a capability manifest. Knows nothing about specific models. */

const el = (tag, attrs = {}, ...kids) => {
  const node = Object.assign(document.createElement(tag), attrs);
  kids.forEach((k) => node.append(k));
  return node;
};

const mb = (bytes) => `${Math.round(bytes / 1048576)} MB`;

/** Duration in seconds of an audio or video file, or null if the browser cannot read it. */
function mediaSeconds(file, kind) {
  return new Promise((resolve) => {
    const m = document.createElement(kind === 'video' ? 'video' : 'audio');
    m.preload = 'metadata';
    m.onloadedmetadata = () => { URL.revokeObjectURL(m.src); resolve(m.duration); };
    m.onerror = () => resolve(null);
    m.src = URL.createObjectURL(file);
  });
}

export class ManifestForm {
  /** @param {HTMLElement} root @param {import('./api/ApiClient.js').Manifest} manifest */
  constructor(root, manifest) {
    this.root = root;
    this.manifest = manifest;
    this.fields = {}; // name -> input element
    this.errors = {}; // name -> error element
    this.#render();
  }

  #field(name, label, control, hint) {
    const err = el('div', { className: 'error', hidden: true });
    this.errors[name] = err;
    return el('label', { className: 'field' }, el('span', { textContent: label }),
      control, ...(hint ? [el('small', { textContent: hint })] : []), err);
  }

  #render() {
    const { inputs, limits, params } = this.manifest;
    this.root.replaceChildren();

    const mode = this.manifest.prompt ?? 'required';
    const max = limits.prompt_max_chars ?? 500;
    this.fields.prompt = el('textarea', { rows: 3, maxLength: max, hidden: mode === 'none' });
    if (mode !== 'none') {
      this.root.append(this.#field('prompt', 'Prompt', this.fields.prompt,
        `${mode === 'optional' ? 'Optional, up' : 'Up'} to ${max} characters`));
    } else {
      this.errors.prompt = el('div', { className: 'error', hidden: true }); // submit errors still need a home
      this.root.append(this.errors.prompt);
    }

    for (const slot of inputs) {
      const many = (slot.max_files ?? 1) > 1;
      const input = el('input', { type: 'file', accept: slot.mime.join(','), multiple: many });
      this.fields[slot.name] = input;
      const hint = [slot.required ? 'Required' : 'Optional', slot.kind,
        many && `up to ${slot.max_files} files`, `max ${mb(slot.max_bytes)} each`,
        slot.max_seconds && `max ${slot.max_seconds}s`].filter(Boolean).join(', ');
      this.root.append(this.#field(slot.name, slot.label, input, hint));
    }

    if (params?.length) {
      const adv = el('details', { className: 'advanced' }, el('summary', { textContent: 'Advanced' }));
      for (const p of params) {
        const control = this.#paramControl(p);
        this.fields[`param:${p.name}`] = control;
        adv.append(this.#field(`param:${p.name}`, p.name.replace(/_/g, ' '), control));
      }
      this.root.append(adv);
    }
  }

  #paramControl(p) {
    if (p.type === 'bool') return el('input', { type: 'checkbox', checked: !!p.default });
    if (p.type === 'enum') {
      const sel = el('select');
      p.values.forEach((v) => sel.append(el('option', { value: v, textContent: v, selected: v === p.default })));
      return sel;
    }
    if (p.type === 'string') return el('input', { type: 'text', value: p.default ?? '', maxLength: p.max_chars ?? 200 });
    return el('input', {
      type: 'number', value: p.default, min: p.min, max: p.max, step: p.type === 'int' ? 1 : 'any',
    });
  }

  #paramValue(p) {
    const c = this.fields[`param:${p.name}`];
    if (p.type === 'bool') return c.checked;
    if (p.type === 'enum') return c.value;
    if (p.type === 'string') return c.value.trim();
    return p.type === 'int' ? parseInt(c.value, 10) : parseFloat(c.value);
  }

  /** @returns {{prompt: string, files: Object<string, File[]>, params: object}} */
  values() {
    const files = {};
    for (const slot of this.manifest.inputs) {
      const list = [...this.fields[slot.name].files];
      if (list.length) files[slot.name] = list;
    }
    const params = Object.fromEntries((this.manifest.params ?? []).map((p) => [p.name, this.#paramValue(p)]));
    return { prompt: this.fields.prompt.value.trim(), files, params };
  }

  clearErrors() {
    Object.values(this.errors).forEach((e) => { e.hidden = true; e.textContent = ''; });
  }

  showError(field, message) {
    const e = this.errors[field] ?? this.errors.prompt;
    e.textContent = message;
    e.hidden = false;
  }

  /** Client-side mirror of the API's manifest checks. @returns {Promise<boolean>} true if valid */
  async validate() {
    this.clearErrors();
    const { inputs, params } = this.manifest;
    const v = this.values();
    let ok = true;
    const fail = (f, m) => { this.showError(f, m); ok = false; };

    if ((this.manifest.prompt ?? 'required') === 'required' && !v.prompt) fail('prompt', 'Prompt is required.');

    for (const slot of inputs) {
      const list = v.files[slot.name] ?? [];
      if (!list.length) { if (slot.required) fail(slot.name, `A ${slot.kind} file is required.`); continue; }
      if (list.length > (slot.max_files ?? 1)) { fail(slot.name, `At most ${slot.max_files ?? 1} file(s).`); continue; }
      for (const file of list) {
        let problem = null;
        if (!slot.mime.includes(file.type)) problem = `${file.name}: unsupported type ${file.type || 'unknown'}.`;
        else if (file.size > slot.max_bytes) problem = `${file.name}: larger than ${mb(slot.max_bytes)}.`;
        else if (slot.max_seconds && slot.kind !== 'image') {
          const secs = await mediaSeconds(file, slot.kind);
          if (secs === null) problem = `${file.name}: could not read this ${slot.kind} file.`;
          else if (secs > slot.max_seconds) problem = `${file.name}: longer than ${slot.max_seconds}s.`;
        }
        if (problem) { fail(slot.name, problem); break; }
      }
    }

    for (const p of params ?? []) {
      const val = v.params[p.name];
      if ((p.type === 'int' || p.type === 'float') && (Number.isNaN(val) || val < p.min || val > p.max)) {
        fail(`param:${p.name}`, `Enter a number from ${p.min} to ${p.max}.`);
      }
    }
    return ok;
  }

  reset() {
    this.#render();
  }
}
