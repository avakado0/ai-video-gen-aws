import { Auth } from './auth.js';
import { RealApiClient } from './api/RealApiClient.js';
import { MockApiClient } from './api/MockApiClient.js';
import { ManifestForm } from './form.js';
import { JobList } from './jobs.js';

const $ = (id) => document.getElementById(id);

async function createApi(config) {
  if (config.mock) {
    const name = new URLSearchParams(location.search).get('manifest') ?? 'text';
    $('mock-banner').hidden = false;
    const shapes = MockApiClient.shapes;
    $('mock-manifest').replaceChildren(...shapes.map((s) => Object.assign(
      document.createElement('option'), { value: s.name, textContent: s.label, selected: s.name === name })));
    $('mock-help').textContent = (shapes.find((s) => s.name === name) ?? shapes[0]).help;
    $('mock-manifest').addEventListener('change', (e) => {
      location.search = `?manifest=${e.target.value}`;
    });
    return new MockApiClient(name);
  }
  const auth = new Auth(config.cognito);
  await auth.handleRedirect();
  return new RealApiClient(config.apiBaseUrl, auth);
}

async function main() {
  const config = await (await fetch('config.json')).json();
  const api = await createApi(config);

  const user = await api.getUser();
  $('login').hidden = !!user;
  $('app').hidden = !user;
  $('logout').hidden = !user;
  $('login-btn').addEventListener('click', async () => { await api.login(); location.reload(); });
  $('logout').addEventListener('click', async () => { await api.logout(); location.reload(); });
  if (!user) return;
  $('who').textContent = user.name;

  const manifest = await api.getConfig();
  $('model').textContent = manifest.model ?? '';
  const form = new ManifestForm($('fields'), manifest);
  const jobs = new JobList($('jobs'), api, config.pollSeconds);
  jobs.start();

  $('job-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const submit = $('submit');
    if (!(await form.validate())) return;
    submit.disabled = true;
    const status = $('submit-status');
    try {
      const { prompt, files, params } = form.values();
      let jobId;
      for (const [slot, list] of Object.entries(files)) {
        for (const [i, file] of list.entries()) {
          status.textContent = `Uploading ${slot.replace(/_/g, ' ')} ${list.length > 1 ? `${i + 1}/${list.length}` : ''}…`;
          jobId = await api.uploadInput(slot, i, file, jobId);
        }
      }
      status.textContent = 'Submitting…';
      const counts = Object.fromEntries(Object.entries(files).map(([slot, list]) => [slot, list.length]));
      await api.createJob({ jobId, prompt, params, inputs: counts });
      status.textContent = '';
      form.reset();
      jobs.start();
    } catch (e) {
      status.textContent = '';
      form.showError('prompt', e.message);
    } finally {
      submit.disabled = false;
    }
  });
}

main().catch((e) => { document.body.prepend(Object.assign(document.createElement('p'), { className: 'error', textContent: e.message })); });
