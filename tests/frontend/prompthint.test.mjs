// Run: node --test tests/frontend/prompthint.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { hasHint, tipsShown, setTipsShown, STORAGE_KEY } from '../../frontend/js/prompthint.js';

const fakeStorage = (init = {}) => {
  const data = { ...init };
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    data,
  };
};

test('hasHint: needs a non-empty list and a prompt field', () => {
  assert.equal(hasHint({ prompt_hint: ['a'] }), true);
  assert.equal(hasHint({ prompt: 'optional', prompt_hint: ['a'] }), true);
  assert.equal(hasHint({}), false);
  assert.equal(hasHint({ prompt_hint: [] }), false);
  assert.equal(hasHint({ prompt_hint: 'text' }), false);
  assert.equal(hasHint({ prompt: 'none', prompt_hint: ['a'] }), false);
  assert.equal(hasHint(null), false);
});

test('tips are shown by default', () => {
  assert.equal(tipsShown(fakeStorage()), true);
});
test('the choice is remembered', () => {
  const s = fakeStorage();
  setTipsShown(s, false);
  assert.equal(tipsShown(s), false);
  setTipsShown(s, true);
  assert.equal(tipsShown(s), true);
  assert.equal(s.data[STORAGE_KEY], 'true');
});
test('a garbage stored value falls back to shown', () => {
  assert.equal(tipsShown(fakeStorage({ [STORAGE_KEY]: 'maybe' })), true);
});
test('storage that throws (private mode) does not break the form', () => {
  const boom = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  assert.equal(tipsShown(boom), true);
  assert.doesNotThrow(() => setTipsShown(boom, false));
  assert.equal(tipsShown(undefined), true);
  assert.doesNotThrow(() => setTipsShown(undefined, false));
});
