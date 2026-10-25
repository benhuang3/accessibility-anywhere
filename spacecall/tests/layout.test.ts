import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fitScreen, toggleScreenZoom } from '../src/lib/screenPolicy.ts';

test('portrait fits a whole 16:10 screen to the available phone width', () => {
  assert.deepEqual(fitScreen(390, 500, 1600, 1000), { width: 390, height: 243.75 });
});

test('landscape fits a whole 16:9 screen to the height left beside the rail', () => {
  const fitted = fitScreen(600, 300, 1920, 1080);
  assert.equal(fitted.height, 300);
  assert.ok(Math.abs(fitted.width - 533.3333333333334) < 1e-9);
});

test('fit keeps every edge visible for wide, tall and square screens in both orientations', () => {
  for (const [availableWidth, availableHeight] of [[296, 300], [369, 470], [610, 340], [500, 200]]) {
    for (const [width, height] of [[1920, 1080], [1600, 1000], [1080, 1920], [1000, 1000], [2560, 720]]) {
      const fitted = fitScreen(availableWidth, availableHeight, width, height);
      assert.ok(fitted.width <= availableWidth + 1e-9);
      assert.ok(fitted.height <= availableHeight + 1e-9);
      assert.ok(Math.abs(fitted.width / fitted.height - width / height) < 1e-9);
      assert.ok(Math.abs(fitted.width - availableWidth) < 1e-9 || Math.abs(fitted.height - availableHeight) < 1e-9);
    }
  }
});

test('unmeasured, zero and invalid dimensions have no drawable area', () => {
  for (const value of [0, -1, NaN, Infinity]) {
    assert.deepEqual(fitScreen(value, 300, 1600, 1000), { width: 0, height: 0 });
    assert.deepEqual(fitScreen(390, value, 1600, 1000), { width: 0, height: 0 });
    assert.deepEqual(fitScreen(390, 300, value, 1000), { width: 0, height: 0 });
    assert.deepEqual(fitScreen(390, 300, 1600, value), { width: 0, height: 0 });
  }
});

test('double-tap toggles exactly between fit and 2x without accumulating zoom', () => {
  assert.equal(toggleScreenZoom(1), 2);
  assert.equal(toggleScreenZoom(2), 1);
  assert.equal(toggleScreenZoom(toggleScreenZoom(1)), 1);
});
