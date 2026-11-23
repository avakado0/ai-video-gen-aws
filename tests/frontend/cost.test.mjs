// Run: node --test tests/frontend/cost.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { formatCost, costNote } from '../../frontend/js/cost.js';

test('formatCost: cents above a cent, finer below, never a bare zero', () => {
  assert.equal(formatCost(0.0712), '~$0.07');
  assert.equal(formatCost(1.234), '~$1.23');
  assert.equal(formatCost(0.0041), '~$0.004');
  assert.equal(formatCost(0.0004), '<$0.001');
  assert.equal(formatCost(0), '<$0.001');
});

test('formatCost: unknown values give nothing', () => {
  for (const v of [null, undefined, NaN, 'x', -1]) assert.equal(formatCost(v), '');
});

test('costNote: cost, tries and the GPU-only label', () => {
  assert.equal(costNote({ status: 'ready', cost_usd: 0.0712, attempts: 2 }), '~$0.07 · 2 tries · GPU only');
  assert.equal(costNote({ status: 'ready', cost_usd: 0.0412, attempts: 1 }), '~$0.04 · 1 try · GPU only');
});

test('costNote: failed jobs show what they burned too', () => {
  assert.equal(costNote({ status: 'failed', cost_usd: 0.21, attempts: 3 }), '~$0.21 · 3 tries · GPU only');
});

test('costNote: an unknown price still shows the tries, without the label', () => {
  assert.equal(costNote({ status: 'ready', cost_usd: null, attempts: 2 }), '2 tries');
});

test('costNote: nothing known, or a pending job, shows nothing', () => {
  assert.equal(costNote({ status: 'ready', cost_usd: null, attempts: null }), '');
  assert.equal(costNote({ status: 'ready' }), '');
  assert.equal(costNote({ status: 'pending', cost_usd: 0.1, attempts: 1 }), '');
});

test('costNote: a cost without a try count is still shown', () => {
  assert.equal(costNote({ status: 'ready', cost_usd: 0.05, attempts: null }), '~$0.05 · GPU only');
});
