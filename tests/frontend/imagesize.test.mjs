// Run: node --test tests/frontend/imagesize.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { shortSideProblem, minSideHint } from '../../frontend/js/imagesize.js';

test('a shorter side under the minimum is refused, with the file name and both numbers', () => {
  const msg = shortSideProblem('photo.jpg', 800, 300, 512);
  assert.match(msg, /photo\.jpg/);
  assert.match(msg, /300/);
  assert.match(msg, /512/);
});
test('exactly the minimum passes, and so does anything larger', () => {
  assert.equal(shortSideProblem('a.png', 512, 512, 512), null);
  assert.equal(shortSideProblem('a.png', 1920, 1080, 512), null);
});
test('the shorter side counts, whichever way the photo is turned', () => {
  assert.notEqual(shortSideProblem('a.png', 300, 900, 512), null);
  assert.notEqual(shortSideProblem('a.png', 900, 300, 512), null);
  assert.equal(shortSideProblem('a.png', 600, 900, 512), null);
});
test('no limit in the manifest means no check', () => {
  assert.equal(shortSideProblem('a.png', 10, 10, undefined), null);
  assert.equal(shortSideProblem('a.png', 10, 10, null), null);
});
test('the hint names the limit, or is empty without one', () => {
  assert.equal(minSideHint(512), 'at least 512 px on the shorter side');
  assert.equal(minSideHint(undefined), '');
});
