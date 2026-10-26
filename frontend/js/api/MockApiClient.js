import { ApiClient } from './ApiClient.js';

const PENDING_MS = 4000;
const SAMPLE_CLIP = new URL('../../assets/sample.mp4', import.meta.url).href;
const MANIFESTS = {
  text: 'text.json', 'text-image': 'text-image.json', references: 'references.json', 'first-last': 'first-last.json',
  video: 'video.json', 'video-audio': 'video-audio.json', avatar: 'avatar.json',
};

const uuid = () => crypto.randomUUID();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * In-memory stand-in for the backend. Job state mirrors the S3 marker semantics:
 * a job is pending until PENDING_MS has passed, then ready (or failed for prompt "fail").
 * Jobs persist in sessionStorage so a reload keeps them, like GET /jobs would.
 */
export class MockApiClient extends ApiClient {
  /** @param {string} manifestName key of MANIFESTS */
  constructor(manifestName = 'text') {
    super();
    this.manifestName = MANIFESTS[manifestName] ? manifestName : 'text';
    this.uploads = new Map(); // jobId -> {slot: File[]}
    this.jobs = this.#load();
    this.signedIn = sessionStorage.getItem('mock_user') === '1';
  }

  /** Mock model shapes for the picker: what the form will ask for. */
  static get shapes() {
    return [
      { name: 'text', label: 'Text to video', help: 'Prompt only. No uploads, no advanced settings.' },
      { name: 'text-image', label: 'Text + image to video', help: 'Prompt plus an optional starting image. Advanced: seed.' },
      { name: 'references', label: 'Reference images to video', help: 'Prompt plus 1 to 4 reference images in one slot. Advanced: negative prompt (a free-text param).' },
      { name: 'first-last', label: 'First / last frame to video', help: 'Prompt plus a required first-frame image and an optional last-frame image.' },
      { name: 'video', label: 'Video + text to video', help: 'Prompt plus a required source video (max 10 s) to restyle or edit. Advanced: strength.' },
      { name: 'video-audio', label: 'Video + audio (lip-sync)', help: 'Prompt plus a required source video and a required audio clip, each max 30 s.' },
      { name: 'avatar', label: 'Avatar (image + audio)', help: 'No prompt: only a required face image and a required speech audio clip (max 60 s). Advanced: resolution, guidance, enhance face.' },
    ];
  }

  #load() {
    try { return JSON.parse(sessionStorage.getItem('mock_jobs')) ?? {}; } catch { return {}; }
  }

  #save() { sessionStorage.setItem('mock_jobs', JSON.stringify(this.jobs)); }

  async getUser() { return this.signedIn ? { name: 'mock@example.com' } : null; }

  async login() {
    this.signedIn = true;
    sessionStorage.setItem('mock_user', '1');
  }

  async logout() {
    this.signedIn = false;
    sessionStorage.removeItem('mock_user');
  }

  async getConfig() {
    await sleep(100);
    const res = await fetch(new URL(`./../mock/manifests/${MANIFESTS[this.manifestName]}`, import.meta.url));
    return res.json();
  }

  async uploadInput(slot, index, file, jobId) {
    await sleep(300);
    const id = jobId ?? uuid();
    const held = this.uploads.get(id) ?? {};
    held[slot] = Object.assign([...(held[slot] ?? [])], { [index]: file });
    this.uploads.set(id, held);
    return id;
  }

  async createJob({ jobId, prompt, params, inputs }) {
    await sleep(200);
    const id = jobId ?? uuid();
    const held = this.uploads.get(id) ?? {};
    for (const [slot, count] of Object.entries(inputs)) {
      if ((held[slot]?.length ?? 0) < count) throw new Error(`Input "${slot}" was not fully uploaded`);
    }
    this.jobs[id] = { id, prompt, params, created: new Date().toISOString(), submittedAt: Date.now() };
    this.#save();
    this.uploads.delete(id); // worker deletes inputs after a successful job
    return id;
  }

  #view(job) {
    const done = Date.now() - job.submittedAt >= PENDING_MS;
    const base = { id: job.id, prompt: job.prompt, created: job.created };
    if (!done) return { ...base, status: 'pending' };
    if (job.prompt.trim().toLowerCase() === 'fail') {
      return { ...base, status: 'failed', reason: { code: 'invalid_input', field: 'prompt', message: 'Mock failure requested.' } };
    }
    return { ...base, status: 'ready', url: SAMPLE_CLIP };
  }

  async listJobs() {
    await sleep(100);
    return Object.values(this.jobs)
      .sort((a, b) => b.submittedAt - a.submittedAt)
      .map((j) => { const { url, ...rest } = this.#view(j); return rest; }); // list carries no URLs
  }

  async getJob(id) {
    await sleep(100);
    const job = this.jobs[id];
    if (!job) { const e = new Error('Job not found'); e.status = 404; throw e; }
    return this.#view(job);
  }

  async deleteJob(id) {
    delete this.jobs[id];
    this.#save();
  }
}
