/**
 * Unit tests for format.js.
 *
 * Run with:  node --test "frontend/js/*.test.js"
 *
 * Quote the glob so the shell doesn't expand it. A bare directory argument
 * (`node --test frontend/js`) is treated as a module to execute, not a suite to
 * discover, and fails with MODULE_NOT_FOUND.
 *
 * Note: a recursive glob cannot be written in a block comment — the "*" and "/"
 * of the second path segment close the comment early, which is a syntax error
 * that only surfaces when the file is parsed.
 *
 * node --test handles ES modules natively, so there is no npm install, no build
 * step and no test framework to vendor — which keeps the zero-dependency
 * deployment story intact.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  basisLabel,
  basisShort,
  escapeHtml,
  formatCount,
  formatINR,
  formatMs,
  normaliseNumbersInText,
  pageLabel,
} from './format.js';

test('formatINR groups Indian-style', () => {
  assert.equal(formatINR(1000, 0), '1,000');
  assert.equal(formatINR(100000, 0), '1,00,000');
  assert.equal(formatINR(10000000, 0), '1,00,00,000');
  assert.equal(formatINR(225712, 0), '2,25,712');
  assert.equal(formatINR(162990, 0), '1,62,990');
  assert.equal(formatINR(255324, 0), '2,55,324');
});

test('formatINR handles short numbers without separators', () => {
  assert.equal(formatINR(0, 0), '0');
  assert.equal(formatINR(7, 0), '7');
  assert.equal(formatINR(99, 0), '99');
  assert.equal(formatINR(999, 0), '999');
});

test('formatINR handles decimals', () => {
  assert.equal(formatINR(22575712.5), '2,25,75,712.50');
  assert.equal(formatINR(1000), '1,000.00');
  assert.equal(formatINR(0.5), '0.50');
});

test('formatINR handles negatives', () => {
  assert.equal(formatINR(-225712, 0), '-2,25,712');
  assert.equal(formatINR(-1000.5), '-1,000.50');
  assert.equal(formatINR(-999, 0), '-999');
});

test('formatINR returns an em dash for missing values, not zero', () => {
  // A missing figure and a zero figure mean different things in a financial
  // statement; rendering one as the other would be a real error.
  for (const missing of [null, undefined, '', NaN, Infinity, 'abc']) {
    assert.equal(formatINR(missing), '—', `for ${String(missing)}`);
  }
  assert.equal(formatINR(0, 0), '0');
});

test('formatINR accepts numeric strings', () => {
  assert.equal(formatINR('225712', 0), '2,25,712');
});

test('formatCount and formatMs are integer-valued', () => {
  assert.equal(formatCount(6412), '6,412');
  assert.equal(formatCount(1847), '1,847');
  assert.equal(formatMs(1033.4), '1,033');
  assert.equal(formatMs(142.6), '143');
  assert.equal(formatMs(null), '—');
});

// ── normaliseNumbersInText ──────────────────────────────────────────────────
// Infosys prints Western grouping in its consolidated statements and Indian
// grouping in its standalone statements, in the same report. Both arrive in
// answer text, so this has to fix one without touching the other.

test('regroups Western-grouped figures', () => {
  assert.equal(normaliseNumbersInText('Revenue was 162,990 crore'),
               'Revenue was 1,62,990 crore');
  assert.equal(normaliseNumbersInText('153,670'), '1,53,670');
  assert.equal(normaliseNumbersInText('1,234,567'), '12,34,567');
  assert.equal(normaliseNumbersInText('255,324 and 240,893'),
               '2,55,324 and 2,40,893');
});

test('leaves already-Indian-grouped figures untouched', () => {
  // The regression the lookbehind exists for: without it, `1,62,990` matches its
  // own tail `62,990` and corrupts a figure that was already correct.
  for (const already of ['1,62,990', '1,36,592', '2,25,712', '1,00,00,000',
                         '12,34,567', '1,41,374']) {
    assert.equal(normaliseNumbersInText(already), already);
  }
});

test('leaves figures with no separators alone', () => {
  // Without commas there is no way to tell a rupee figure from an identifier.
  assert.equal(normaliseNumbersInText('16299000'), '16299000');
  assert.equal(normaliseNumbersInText('2025'), '2025');
});

test('does not corrupt dates, years, pages or note numbers', () => {
  const samples = [
    'FY2024-25',
    'for the year ended March 31, 2025',
    '[Page 276]',
    'Note 2.18',
    'p.276 / 369',
    'grew by 6.0% over the year',
    '44th Annual General Meeting',
    'Rs 25/- per share',
  ];
  for (const sample of samples) {
    assert.equal(normaliseNumbersInText(sample), sample, sample);
  }
});

test('preserves decimals when regrouping', () => {
  assert.equal(normaliseNumbersInText('162,990.25'), '1,62,990.25');
  assert.equal(normaliseNumbersInText('1,234,567.89'), '12,34,567.89');
});

test('handles a realistic mixed answer', () => {
  const input =
    'Infosys consolidated revenue for FY2024-25 was 162,990 crore [Page 276]. '
    + 'The FY2023-24 comparative column shows 153,670 crore. '
    + 'Standalone revenue was 1,36,592 crore [Page 196] on March 31, 2025.';
  const output = normaliseNumbersInText(input);

  assert.ok(output.includes('1,62,990 crore'));
  assert.ok(output.includes('1,53,670 crore'));
  assert.ok(output.includes('1,36,592 crore'), 'standalone figure must be unchanged');
  assert.ok(output.includes('FY2024-25'));
  assert.ok(output.includes('[Page 276]'));
  assert.ok(output.includes('March 31, 2025'));
  assert.ok(!output.includes('162,990'), 'Western grouping must not survive');
});

test('normaliseNumbersInText tolerates empty input', () => {
  assert.equal(normaliseNumbersInText(''), '');
  assert.equal(normaliseNumbersInText(null), null);
});

// ── Labels ──────────────────────────────────────────────────────────────────

test('basis labels state an unknown rather than defaulting', () => {
  assert.equal(basisLabel('consolidated'), 'Consolidated');
  assert.equal(basisLabel('standalone'), 'Standalone');
  // Never blank, and never silently "Consolidated" — that is the whole point.
  assert.equal(basisLabel(null), 'Basis not determined');
  assert.equal(basisLabel(undefined), 'Basis not determined');
  assert.equal(basisShort(null), 'Basis unknown');
});

test('escapeHtml neutralises markup', () => {
  assert.equal(escapeHtml('<script>alert("x")</script>'),
               '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;');
  assert.equal(escapeHtml("it's"), 'it&#39;s');
  assert.equal(escapeHtml(null), '');
});

test('pageLabel pads so citation lists align', () => {
  assert.equal(pageLabel(69), 'p.069');
  assert.equal(pageLabel(276), 'p.276');
  assert.equal(pageLabel(1), 'p.001');
  assert.equal(pageLabel(null), 'p.?');
});
