/** Job list: renders GET /jobs and polls pending jobs until they settle. */

export class JobList {
  /**
   * @param {HTMLElement} root
   * @param {import('./api/ApiClient.js').ApiClient} api
   * @param {number} pollSeconds
   */
  constructor(root, api, pollSeconds = 3) {
    this.root = root;
    this.api = api;
    this.pollMs = pollSeconds * 1000;
    this.timer = null;
  }

  start() {
    this.stop();
    this.refresh();
  }

  stop() { clearTimeout(this.timer); }

  async refresh() {
    let jobs;
    try { jobs = await this.api.listJobs(); } catch (e) { this.#renderError(e); return; }
    await this.#render(jobs);
    if (jobs.some((j) => j.status === 'pending')) this.timer = setTimeout(() => this.refresh(), this.pollMs);
  }

  #renderError(e) {
    this.root.replaceChildren(Object.assign(document.createElement('p'), { className: 'error', textContent: e.message }));
  }

  async #render(jobs) {
    if (!jobs.length) {
      this.root.replaceChildren(Object.assign(document.createElement('p'), { className: 'muted', textContent: 'No jobs yet.' }));
      return;
    }
    // The list has no download URLs and no failure reasons; fetch the detail for settled jobs.
    const detailed = await Promise.all(jobs.map((j) => (j.status === 'pending' ? j : this.api.getJob(j.id).catch(() => j))));
    this.root.replaceChildren(...detailed.map((j) => this.#row(j)));
  }

  #row(job) {
    const li = document.createElement('article');
    li.className = `job ${job.status}`;
    const title = document.createElement('p');
    title.className = 'job-prompt';
    title.textContent = job.prompt || '(no prompt)';
    const status = document.createElement('span');
    status.className = 'badge';
    status.textContent = job.status === 'pending' ? 'Generating…' : job.status === 'ready' ? 'Ready' : 'Failed';
    li.append(status, title);

    if (job.status === 'ready' && job.url) {
      const video = Object.assign(document.createElement('video'), { src: job.url, controls: true, preload: 'metadata' });
      const dl = Object.assign(document.createElement('a'), { href: job.url, download: `${job.id}.mp4`, textContent: 'Download' });
      dl.addEventListener('click', () => setTimeout(() => this.#confirmDownload(job.id), 1000));
      li.append(video, dl);
    }
    if (job.status === 'failed') {
      const why = document.createElement('p');
      why.className = 'error';
      const r = job.reason ?? {};
      why.textContent = r.message ?? 'Generation failed.';
      if (r.field) why.textContent = `${r.field}: ${why.textContent}`;
      li.append(why);
    }
    if (job.status !== 'pending') {
      const del = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Remove' });
      del.addEventListener('click', async () => { await this.api.deleteJob(job.id); this.refresh(); });
      li.append(del);
    }
    return li;
  }

  /** The browser has started the download: tell the API so it deletes the clip (spec 3B). */
  async #confirmDownload(id) {
    await this.api.deleteJob(id);
    this.refresh();
  }
}
