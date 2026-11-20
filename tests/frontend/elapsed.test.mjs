// Run: node --test tests/frontend/elapsed.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { formatElapsed, pendingNote } from '../../frontend/js/elapsed.js';

const T0 = '2026-11-20T10:00:00.000Z';
const at = (s) => Date.parse(T0) + s * 1000;

test('seconds under a minute', () => {
  assert.equal(formatElapsed(T0, at(0)), '0 s');
  assert.equal(formatElapsed(T0, at(42)), '42 s');
});
test('minutes from 60 s, rounded down', () => {
  assert.equal(formatElapsed(T0, at(60)), '1 min');
  assert.equal(formatElapsed(T0, at(185)), '3 min');
});
test('clock skew or a missing date gives no elapsed text', () => {
  assert.equal(formatElapsed(T0, at(-5)), '0 s');
  assert.equal(formatElapsed(undefined, at(5)), '');
  assert.equal(formatElapsed('not a date', at(5)), '');
});
test('pending note shows elapsed time and the idle warning', () => {
  const note = pendingNote(T0, at(90));
  assert.match(note, /1 min/);
  assert.match(note, /up to 20 minutes/);
});
test('pending note still shows the warning when the date is missing', () => {
  const note = pendingNote(undefined, at(90));
  assert.match(note, /up to 20 minutes/);
  assert.doesNotMatch(note, /NaN/);
});
