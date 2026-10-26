import { ApiClient } from './ApiClient.js';

export class RealApiClient extends ApiClient {
  /** @param {string} baseUrl @param {import('../auth.js').Auth} auth */
  constructor(baseUrl, auth) {
    super();
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.auth = auth;
  }

  async getUser() { return this.auth.user(); }
  async login() { return this.auth.login(); }
  async logout() { return this.auth.logout(); }

  async #call(method, path, body) {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { Authorization: `Bearer ${this.auth.token()}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      const err = new Error(detail.detail ?? `HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return res.status === 204 ? null : res.json();
  }

  getConfig() { return this.#call('GET', '/config'); }

  async uploadInput(slot, index, file, jobId) {
    const post = await this.#call('POST', '/uploads', {
      slot, index, mime: file.type, size: file.size, ...(jobId ? { job_id: jobId } : {}),
    });
    const form = new FormData();
    Object.entries(post.fields).forEach(([k, v]) => form.append(k, v));
    form.append('file', file); // must be last
    const res = await fetch(post.url, { method: 'POST', body: form });
    if (!res.ok) throw new Error(`Upload failed (HTTP ${res.status})`);
    return post.job_id;
  }

  async createJob({ jobId, prompt, params, inputs }) {
    const out = await this.#call('POST', '/jobs', { job_id: jobId, prompt, params, inputs });
    return out.job_id;
  }

  async listJobs() { return (await this.#call('GET', '/jobs')).jobs; }
  getJob(id) { return this.#call('GET', `/jobs/${encodeURIComponent(id)}`); }
  async deleteJob(id) { await this.#call('DELETE', `/jobs/${encodeURIComponent(id)}`); }
}
