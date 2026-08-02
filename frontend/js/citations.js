/**
 * Provenance chips, and rewriting inline citation markers into them.
 *
 * The chip carries DOCUMENT provenance — entity, fiscal year, basis, page. That
 * is which report a figure was published in.
 *
 * It deliberately does NOT carry which year the figure itself belongs to.
 * Financial statements print the current year beside comparative columns, so one
 * table holds figures for two years, and only the model can read the column
 * header out of a chunk. That attribution lives in the answer prose. Merging the
 * two would be the exact conflation the prompt works to prevent: a chip reading
 * "FY2024-25" beside a number lifted from the FY2023-24 comparative column would
 * be a confident, well-designed lie.
 *
 * Interaction is driven by the structured `sources` array, never by parsing
 * prose. The inline "[Page N]" markers stay for readability, but they are only a
 * lookup key — if one has no matching source it is left as plain text rather than
 * rendered as a citation that goes nowhere.
 */

import { basisShort, escapeHtml } from './format.js';

/** Matches the inline markers the prompt asks the model to emit. */
const MARKER = /\[Page\s+(\d+)\]/gi;

/**
 * Build one chip.
 *
 * `interactive` is false when the document has no stored PDF — a citation that
 * cannot open must not look clickable.
 */
export function chipHtml(source, { interactive = true, pageOnly = false } = {}) {
  const canOpen = interactive && Boolean(source.doc_id);
  const tag = canOpen ? 'button' : 'span';
  const classes = `prov${canOpen ? '' : ' inert'}`;

  const basisClass = source.basis ? '' : ' unknown';
  const parts = pageOnly
    ? [`<span class="p-pg">p.${escapeHtml(source.page_number)}</span>`]
    : [
      `<span class="p-ent">${escapeHtml(source.entity || 'Entity ?')}</span>`,
      `<span class="p-fy">${escapeHtml(source.fiscal_year || 'FY ?')}</span>`,
      `<span class="p-bas${basisClass}">${escapeHtml(basisShort(source.basis))}</span>`,
      `<span class="p-pg">p.${escapeHtml(source.page_number)}</span>`,
    ];

  const label = [
    source.entity || 'Unknown entity',
    source.fiscal_year || 'unknown fiscal year',
    basisShort(source.basis).toLowerCase(),
    `page ${source.page_number}`,
  ].join(', ');

  const attrs = [
    `class="${classes}"`,
    canOpen ? `type="button"` : '',
    canOpen ? `data-doc-id="${escapeHtml(source.doc_id)}"` : '',
    canOpen ? `data-page="${escapeHtml(source.page_number)}"` : '',
    source.chunk_id ? `data-chunk-id="${escapeHtml(source.chunk_id)}"` : '',
    canOpen
      ? `title="Open ${escapeHtml(source.doc_name)} at page ${escapeHtml(source.page_number)}"`
      : `title="No stored PDF for ${escapeHtml(source.doc_name)}"`,
    `aria-label="${canOpen ? 'Open source: ' : 'Source (no PDF available): '}${escapeHtml(label)}"`,
  ].filter(Boolean).join(' ');

  return `<${tag} ${attrs}>${parts.join('')}</${tag}>`;
}

/**
 * Index sources by page so inline markers can be resolved.
 *
 * A page number alone is ambiguous once several documents are indexed — p.276
 * exists in every report. Where a marker is ambiguous the first source for that
 * page wins, which is the highest-ranked one, and the full evidence list below
 * the answer disambiguates. Filtering to a single document removes the ambiguity
 * entirely.
 */
function indexByPage(sources) {
  const byPage = new Map();
  for (const source of sources) {
    const key = String(source.page_number);
    if (!byPage.has(key)) byPage.set(key, source);
  }
  return byPage;
}

/**
 * Replace "[Page N]" in already-escaped HTML with chips.
 *
 * Must run AFTER escaping — it emits markup, so anything applied afterwards
 * would escape the chips themselves.
 */
export function linkCitations(escapedHtml, sources, { openableDocIds } = {}) {
  if (!sources || !sources.length) return escapedHtml;
  const byPage = indexByPage(sources);

  return escapedHtml.replace(MARKER, (whole, page) => {
    const source = byPage.get(String(Number(page)));
    // An unmatched marker stays as plain text. Rendering it as a citation that
    // resolves to nothing would be worse than leaving the model's prose alone.
    if (!source) return whole;
    const interactive = !openableDocIds || openableDocIds.has(source.doc_id);
    return chipHtml(source, { interactive });
  });
}

/**
 * Delegated click handling for every chip and evidence row on the page.
 *
 * One listener on the stream rather than per-node handlers, so cards rendered
 * later need no wiring.
 */
export function onCitationActivate(root, handler) {
  root.addEventListener('click', (event) => {
    const target = event.target.closest('[data-doc-id][data-page]');
    if (!target || !root.contains(target)) return;
    handler({
      docId: target.dataset.docId,
      page: Number(target.dataset.page),
      chunkId: target.dataset.chunkId || null,
      trigger: target,
    });
  });
}
