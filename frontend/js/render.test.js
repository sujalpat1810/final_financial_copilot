/**
 * Tests for the answer renderer and citation chips.
 *
 * Run with:  node --test "frontend/js/*.test.js"
 *
 * These run without a DOM: every function under test returns an HTML string, so
 * the assertions are on markup rather than on rendered pixels.
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { chipHtml, linkCitations } from './citations.js';
import { confidenceState, relevanceIsWeak } from './confidence.js';
import { _internal } from './render.js';

const { renderAnswerBody, isNumericCell } = _internal;

const source = (over = {}) => ({
  doc_id: 'abc1234567890def',
  doc_name: 'Infosys FY2024-25',
  chunk_id: 'abc1234567890def_p276_0',
  page_number: 276,
  entity: 'Infosys',
  fiscal_year: 'FY2024-25',
  basis: 'consolidated',
  relevance: 82,
  excerpt: 'Revenue from operations 162,990',
  is_table: true,
  section_title: null,
  rerank_score: 6.4,
  ...over,
});

// ── Confidence presentation ──────────────────────────────────────────────────

test('every confidence level pairs colour with an icon and a word', () => {
  for (const level of ['high', 'moderate', 'low', 'insufficient']) {
    const state = confidenceState(level);
    assert.ok(state.icon, `${level} has no icon`);
    assert.ok(state.label, `${level} has no label`);
    assert.ok(state.description, `${level} has no description`);
  }
});

test('an unrecognised confidence level degrades to caution, not confidence', () => {
  // If the server ever sends a level this build does not know, showing "High"
  // would be actively dangerous.
  const state = confidenceState('something-new');
  assert.equal(state.className, 'low');
  assert.match(state.label, /unknown/i);
});

test('weak relevance is flagged so near-misses do not read as good matches', () => {
  assert.equal(relevanceIsWeak(11), true);
  assert.equal(relevanceIsWeak(82), false);
});

// ── Provenance chips ─────────────────────────────────────────────────────────

test('chip carries entity, fiscal year, basis and page', () => {
  const html = chipHtml(source());
  assert.match(html, /Infosys/);
  assert.match(html, /FY2024-25/);
  assert.match(html, /Consolidated/);
  assert.match(html, /p\.276/);
});

test('undetermined basis is shown as a stated unknown, never blank', () => {
  const html = chipHtml(source({ basis: null }));
  assert.match(html, /Basis unknown/);
  assert.match(html, /p-bas unknown/, 'must carry the warning class');
  assert.doesNotMatch(html, /Consolidated/,
    'must not default to the more commonly quoted basis');
});

test('a chip with no stored PDF is inert and not a button', () => {
  const html = chipHtml(source(), { interactive: false });
  assert.match(html, /class="prov inert"/);
  assert.match(html, /^<span /, 'must not render as a button');
  assert.doesNotMatch(html, /data-doc-id/, 'must not look activatable');
});

test('an openable chip is a button carrying the ids the viewer needs', () => {
  const html = chipHtml(source());
  assert.match(html, /^<button /);
  assert.match(html, /data-doc-id="abc1234567890def"/);
  assert.match(html, /data-page="276"/);
  assert.match(html, /data-chunk-id="abc1234567890def_p276_0"/);
});

test('chips carry an accessible label, not just visual segments', () => {
  assert.match(chipHtml(source()), /aria-label="Open source: Infosys, FY2024-25/);
});

// ── Inline citation linking ──────────────────────────────────────────────────

test('inline markers become chips', () => {
  const html = linkCitations('Revenue was 1,62,990 crore [Page 276].', [source()]);
  assert.match(html, /class="prov"/);
  assert.doesNotMatch(html, /\[Page 276\]/);
});

test('a marker with no matching source stays plain text', () => {
  // Rendering a citation that resolves to nothing would be worse than leaving
  // the model's prose alone.
  const html = linkCitations('See [Page 999].', [source()]);
  assert.match(html, /\[Page 999\]/);
  assert.doesNotMatch(html, /class="prov"/);
});

test('linking is a no-op when there are no sources', () => {
  assert.equal(linkCitations('See [Page 276].', []), 'See [Page 276].');
});

test('markers for documents without a PDF render inert', () => {
  const html = linkCitations('[Page 276]', [source()], { openableDocIds: new Set() });
  assert.match(html, /prov inert/);
});

test('several markers in one answer all resolve', () => {
  const sources = [source(), source({ page_number: 196, basis: 'standalone' })];
  const html = linkCitations('A [Page 276] and B [Page 196].', sources);
  assert.equal((html.match(/class="prov"/g) || []).length, 2);
  assert.match(html, /Standalone/);
});

// ── Answer prose ─────────────────────────────────────────────────────────────

test('paragraphs are separated, not concatenated', () => {
  const html = renderAnswerBody('First line.\n\nSecond line.');
  assert.equal((html.match(/<p>/g) || []).length, 2);
});

test('markup in the model output is escaped', () => {
  const html = renderAnswerBody('<img src=x onerror=alert(1)>');
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

test('bullets become a list', () => {
  const html = renderAnswerBody('Items:\n- one\n- two');
  assert.match(html, /<ul><li>one<\/li><li>two<\/li><\/ul>/);
});

test('bold survives escaping', () => {
  assert.match(renderAnswerBody('the **comparative column**'),
               /<strong>comparative column<\/strong>/);
});

test('digit grouping is normalised in prose', () => {
  // The corpus mixes conventions: Infosys prints Western grouping in its
  // consolidated statements and Indian in its standalone ones.
  const html = renderAnswerBody('Revenue was 162,990 crore.');
  assert.match(html, /1,62,990/);
  assert.doesNotMatch(html, /162,990/);
});

test('already-Indian figures are left alone in prose', () => {
  assert.match(renderAnswerBody('Standalone revenue was 1,36,592 crore.'), /1,36,592/);
});

// ── Tables — the format for an unqualified question ──────────────────────────

const TABLE = [
  '| Entity | Fiscal year | Basis | Revenue |',
  '| --- | --- | --- | --- |',
  '| Infosys | FY2024-25 | Consolidated | 1,62,990 |',
  '| Infosys | FY2024-25 | Standalone | 1,36,592 |',
  '| TCS | FY2024-25 | Not determined | 2,55,324 |',
].join('\n');

test('a markdown table renders as a table, not a wall of prose', () => {
  const html = renderAnswerBody(TABLE);
  assert.match(html, /<table class="ftab">/);
  assert.equal((html.match(/<tr>/g) || []).length, 4, 'header plus three rows');
  assert.doesNotMatch(html, /\|/, 'no pipe characters should survive');
});

test('the divider row is not rendered as data', () => {
  assert.doesNotMatch(renderAnswerBody(TABLE), /---/);
});

test('numeric columns are right-aligned and tabular', () => {
  const html = renderAnswerBody(TABLE);
  assert.match(html, /<td class="num r">1,62,990<\/td>/);
  // A text column must not be right-aligned.
  assert.match(html, /<td>Infosys<\/td>/);
});

test('an undetermined basis is highlighted inside a table', () => {
  assert.match(renderAnswerBody(TABLE), /class="bas-unknown">Not determined/);
});

test('numeric cell detection', () => {
  for (const yes of ['1,62,990', '2,55,324', '162990', '6.0%', '₹1,62,990', '-1,000']) {
    assert.equal(isNumericCell(yes), true, yes);
  }
  for (const no of ['Infosys', 'Consolidated', 'Not determined', 'FY2024-25', '']) {
    assert.equal(isNumericCell(no), false, no);
  }
});

test('prose and a table can coexist in one answer', () => {
  const html = renderAnswerBody(
    'The question is unqualified. Six answers are supported:\n\n'
    + TABLE
    + '\n\nThe last figure could not be attributed to a basis.',
  );
  assert.match(html, /<p>The question is unqualified/);
  assert.match(html, /<table class="ftab">/);
  assert.match(html, /<p>The last figure/);
});

test('empty answer text does not throw', () => {
  assert.equal(renderAnswerBody(''), '');
});
