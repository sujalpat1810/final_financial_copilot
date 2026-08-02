/**
 * Tests for the source-viewer text matching.
 *
 * Run with:  node --test "frontend/js/*.test.js"
 *
 * The governing rule is that a WRONG highlight is worse than none — pointing a
 * chartered accountant at the wrong row of a financial statement is a failure
 * that looks like a feature. So most of these assert that matching REFUSES,
 * rather than that it succeeds.
 *
 * Only the pure functions are tested; rendering needs a canvas and a real PDF.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { locateSpans, matchCandidate } from './viewer.js';

/** PDF.js splits a line into many positioned spans; this mimics that. */
const spans = (...strings) => strings.map((str) => ({ str }));

const PROSE =
  'Revenue from operations for the year ended March 31, 2025 increased in '
  + 'reported terms, with growth led by financial services and manufacturing '
  + 'across the major operating segments of the group.';

// ── matchCandidate: choosing what to search for ──────────────────────────────

test('picks a long distinctive run from prose', () => {
  const candidate = matchCandidate(`First sentence here. ${PROSE}`);
  assert.ok(candidate);
  assert.ok(candidate.length >= 40);
  assert.ok(PROSE.includes(candidate.slice(0, 30)));
});

test('table chunks are never matched', () => {
  // Ingestion reserialises tables as pipe-delimited rows, so the chunk text is
  // not a substring of anything on the page. Any match would be spurious.
  const table = '[TABLE]\nRevenue from operations | 162,990 | 153,670\n[/TABLE]';
  assert.equal(matchCandidate(table, { isTable: true }), null);
  assert.equal(matchCandidate(PROSE, { isTable: true }), null,
    'the flag alone must suppress matching');
});

test('a chunk that is mostly table yields no candidate', () => {
  const chunk = '[TABLE]\nRevenue | 1 | 2\nProfit | 3 | 4\n[/TABLE]\nSee note.';
  assert.equal(matchCandidate(chunk), null);
});

test('the overlap sentence is dropped', () => {
  // Chunking prepends the previous chunk's last sentence, which belongs to a
  // different part of the page — matching on it highlights the wrong paragraph.
  const overlap = 'This trailing sentence came from the previous chunk entirely.';
  const candidate = matchCandidate(`${overlap} ${PROSE}`);
  assert.ok(candidate);
  assert.ok(!candidate.startsWith('This trailing sentence'));
});

test('a single short sentence yields nothing', () => {
  assert.equal(matchCandidate('Revenue grew.'), null);
});

test('empty and missing input are safe', () => {
  assert.equal(matchCandidate(''), null);
  assert.equal(matchCandidate(null), null);
  assert.equal(matchCandidate(undefined), null);
});

// ── locateSpans: refusing to guess ───────────────────────────────────────────

test('locates a run split across several spans', () => {
  // The real case: PDF.js rarely gives a sentence as one span.
  const items = spans('Revenue from operations for the year ',
                      'ended March 31, 2025 increased in ',
                      'reported terms.');
  const hit = locateSpans(items, 'Revenue from operations for the year ended March 31, 2025');
  assert.deepEqual(hit, [0, 1]);
});

test('whitespace differences do not defeat a match', () => {
  const items = spans('Revenue   from\noperations for the year ended March 31, 2025');
  assert.ok(locateSpans(items, 'Revenue from operations for the year ended March 31, 2025').length);
});

test('matching is case-insensitive', () => {
  const items = spans('REVENUE FROM OPERATIONS FOR THE YEAR ENDED MARCH 31, 2025');
  assert.ok(locateSpans(items, 'Revenue from operations for the year ended March 31, 2025').length);
});

test('an ambiguous run matches nothing', () => {
  // "Notes to the standalone financial statements" is a running header on
  // dozens of pages. If it appears twice on ONE page, there is no way to know
  // which occurrence the chunk came from, so neither is highlighted.
  const repeated = 'Notes forming part of the standalone financial statements of the company';
  const items = spans(repeated, ' and again ', repeated);
  assert.deepEqual(locateSpans(items, repeated), []);
});

test('a run that is not on the page matches nothing', () => {
  const items = spans('Something entirely different on this page altogether.');
  assert.deepEqual(locateSpans(items, PROSE), []);
});

test('a short candidate is refused even if present', () => {
  // "Revenue from operations" appears on many pages of one report; a match is
  // not evidence it is THE one.
  const items = spans('Revenue from operations 162,990');
  assert.deepEqual(locateSpans(items, 'Revenue from operations'), []);
});

test('a null candidate matches nothing', () => {
  assert.deepEqual(locateSpans(spans('anything at all here'), null), []);
});

test('no items on the page matches nothing', () => {
  assert.deepEqual(locateSpans([], PROSE), []);
});

test('only the spans actually covered are returned', () => {
  const items = spans('Preamble text that precedes it. ',
                      'Revenue from operations for the year ended March 31, 2025 ',
                      'and trailing text afterwards.');
  const hit = locateSpans(items, 'Revenue from operations for the year ended March 31, 2025');
  assert.deepEqual(hit, [1], 'must not spill into neighbouring spans');
});

// ── End to end: chunk text -> highlighted spans ──────────────────────────────

test('a prose chunk locates itself on its page', () => {
  const chunk = `Overlap sentence from the chunk before. ${PROSE}`;
  const items = spans('Infosys Integrated Annual Report 2024-25', PROSE, 'Page footer 284');
  const hit = locateSpans(items, matchCandidate(chunk));
  assert.deepEqual(hit, [1]);
});

test('a table chunk degrades to no highlight rather than a wrong one', () => {
  const chunk = '[TABLE]\nRevenue from operations | 162,990 | 153,670\n[/TABLE]';
  const items = spans('Revenue from operations', '162,990', '153,670');
  assert.deepEqual(locateSpans(items, matchCandidate(chunk, { isTable: true })), []);
});
