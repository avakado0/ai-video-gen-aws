/**
 * @typedef {{name: string, label: string, kind: 'image'|'audio'|'video', required: boolean, max_files?: number, mime: string[], max_bytes: number, max_seconds?: number}} InputSlot
 * @typedef {{model: string, prompt?: 'required'|'optional'|'none', inputs: InputSlot[], limits: {prompt_max_chars: number}, params: object[]}} Manifest
 * @typedef {{id: string, status: 'pending'|'ready'|'failed', prompt?: string, created?: string,
 *            url?: string, reason?: {code: string, field?: string, message: string}}} Job
 */

/** Interface every backend implementation (real or mock) must satisfy. */
export class ApiClient {
  /** @returns {Promise<{name: string}|null>} the signed-in user, or null */
  async getUser() { throw new Error('not implemented'); }
  async login() { throw new Error('not implemented'); }
  async logout() { throw new Error('not implemented'); }

  /** @returns {Promise<Manifest>} */
  async getConfig() { throw new Error('not implemented'); }

  /**
   * Upload one input file into a manifest slot. The first call of a job omits jobId and gets one back.
   * @param {string} slot manifest input slot name
   * @param {number} index 0-based position within the slot (slots can hold several files)
   * @param {File} file
   * @param {string} [jobId]
   * @returns {Promise<string>} the job id
   */
  async uploadInput(slot, index, file, jobId) { throw new Error('not implemented'); }

  /**
   * @param {{jobId?: string, prompt: string, params: object, inputs: Object<string, number>}} req  inputs = uploaded file count per slot
   * @returns {Promise<string>} the job id
   */
  async createJob(req) { throw new Error('not implemented'); }

  /** @returns {Promise<Job[]>} newest first */
  async listJobs() { throw new Error('not implemented'); }

  /** @returns {Promise<Job>} */
  async getJob(id) { throw new Error('not implemented'); }

  async deleteJob(id) { throw new Error('not implemented'); }
}
