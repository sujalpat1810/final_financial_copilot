/**
 * Source panel: renders the cited page of the original PDF beside the answer.
 *
 * A side panel, not a modal — the answer has to stay visible while the source is
 * open, or the reader loses the thing they were checking.
 *
 * On highlighting
 * ───────────────
 * The retrieved chunk's text is located in the page's text layer and marked.
 * The governing rule is that a WRONG highlight is worse than none: pointing a
 * chartered accountant at the wrong row of a financial statement is a failure
 * that looks like a feature. So matching is conservative, and it degrades
 * silently to page-only whenever it is not confident.
 *
 * Two categories never attempt a match at all:
 *
 *   Table chunks. Ingestion reserialises tables as pipe-delimited rows wrapped
 *   in [TABLE] markers, so the chunk text is not a substring of anything on the
 *   page and could only ever produce a spurious match.
 *
 *   The overlap sentence. Chunking prepends the previous chunk's last sentence,
 *   which belongs to a different part of the page — matching on it would
 *   highlight the wrong paragraph.
 *
 * Character offsets are not available and cannot be reconstructed: the chunker
 * rejoins paragraphs and re-serialises tables, so the chunk text is not a
 * substring of the page text. That is why this is a text search rather than a
 * range lookup.
 */

import { documentFileUrl } from './api.js';
import { escapeHtml } from './format.js';

const PDFJS_PATH = '../vendor/pdfjs/pdf.min.mjs';
const WORKER_PATH = 'vendor/pdfjs/pdf.worker.min.mjs';

// Rendering scale. 1.5 keeps a dense statement page legible in a ~430px panel
// without producing a canvas so large it stalls on a laptop.
const SCALE = 1.5;

// A candidate run must be at least this long to be trusted. Short runs like
// "Revenue from operations" appear on many pages of one report; a match on one
// is not evidence it is THE one.
const MIN_MATCH_CHARS = 40;

let pdfjs = null;          // lazily imported: 425 KB should not load on boot
const documentCache = new Map();
let currentTask = 0;       // guards against a slow render landing after a newer one
let lastTrigger = null;    // element focus returns to on close

const el = (id) => document.getElementById(id);

async function loadPdfjs() {
  if (!pdfjs) {
    pdfjs = await import(PDFJS_PATH);
    // The worker keeps parsing off the main thread; without it a 400-page
    // document freezes the UI while the panel opens.
    pdfjs.GlobalWorkerOptions.workerSrc = new URL(WORKER_PATH, document.baseURI).toString();
  }
  return pdfjs;
}

async function loadDocument(docId) {
  if (!documentCache.has(docId)) {
    const lib = await loadPdfjs();
    documentCache.set(docId, lib.getDocument({ url: documentFileUrl(docId) }).promise);
  }
  return documentCache.get(docId);
}

/* ── Text matching ─────────────────────────────────────────────────────────── */

/** Collapse whitespace so extraction quirks don't defeat a comparison. */
function normalise(text) {
  return text.replace(/\s+/g, ' ').trim().toLowerCase();
}

/**
 * Pick the longest distinctive prose run from a chunk.
 *
 * Skips [TABLE] blocks entirely, and drops the first sentence, which is the
 * overlap carried from the previous chunk and belongs elsewhere on the page.
 * Returns null when nothing long enough survives — the caller then opens the
 * page without a highlight rather than guessing.
 */
export function matchCandidate(chunkText, { isTable = false } = {}) {
  if (!chunkText || isTable) return null;

  const withoutTables = chunkText.replace(/\[TABLE\][\s\S]*?\[\/TABLE\]/g, ' ');
  if (/\[TABLE\]/.test(chunkText) && normalise(withoutTables).length < MIN_MATCH_CHARS) {
    return null;
  }

  const sentences = withoutTables
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);

  // Drop the overlap sentence when there is more to work with.
  const usable = sentences.length > 1 ? sentences.slice(1) : sentences;

  const longest = usable
    .map((s) => s.replace(/\s+/g, ' ').trim())
    .filter((s) => s.length >= MIN_MATCH_CHARS)
    .sort((a, b) => b.length - a.length)[0];

  return longest || null;
}

/**
 * Find which text-layer items cover `candidate`.
 *
 * PDF.js splits a line into many positioned spans, so the search runs against
 * the concatenated string and then maps the hit back to the spans it covers.
 * Returns [] when there is no match, or when the candidate appears more than
 * once on the page — an ambiguous match is not a match.
 */
export function locateSpans(items, candidate) {
  if (!candidate) return [];

  const needle = normalise(candidate);
  if (needle.length < MIN_MATCH_CHARS) return [];

  // Build the page string and remember where each item starts within it.
  let haystack = '';
  const offsets = [];
  for (const item of items) {
    offsets.push(haystack.length);
    haystack += `${item.str} `;
  }
  haystack = haystack.toLowerCase().replace(/\s+/g, ' ');

  const first = haystack.indexOf(needle);
  if (first === -1) return [];
  if (haystack.indexOf(needle, first + 1) !== -1) return [];   // ambiguous

  const last = first + needle.length;
  const covered = [];
  for (let i = 0; i < items.length; i += 1) {
    const start = offsets[i];
    const end = start + items[i].str.length;
    if (end > first && start < last) covered.push(i);
  }
  return covered;
}

/* ── Rendering ─────────────────────────────────────────────────────────────── */

function setHighlightState(located, isTable) {
  const node = el('panelHlState');
  node.hidden = false;
  if (located) {
    node.className = 'hl-state';
    node.innerHTML = '<svg class="i i-sm"><use href="#i-check"/></svg> '
      + 'Located on page — highlighted below';
  } else {
    // Stated either way. A silent absence of highlight leaves the reader
    // wondering whether the tool failed or the text simply isn't there.
    node.className = 'hl-state off';
    node.innerHTML = '<svg class="i i-sm"><use href="#i-warn"/></svg> '
      + (isTable
        ? 'Table extract — cannot be matched to the page layout'
        : 'Could not locate the exact text; showing the page');
  }
}

async function renderPage(pdf, pageNumber, { excerpt, isTable }) {
  const page = await pdf.getPage(pageNumber);
  const viewport = page.getViewport({ scale: SCALE });

  const wrap = document.createElement('div');
  wrap.className = 'pageWrap';
  wrap.style.width = `${viewport.width}px`;
  wrap.style.height = `${viewport.height}px`;

  const canvas = document.createElement('canvas');
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(viewport.width * ratio);
  canvas.height = Math.floor(viewport.height * ratio);
  canvas.style.width = `${viewport.width}px`;
  canvas.style.height = `${viewport.height}px`;
  wrap.appendChild(canvas);

  await page.render({
    canvasContext: canvas.getContext('2d'),
    viewport,
    transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0],
  }).promise;

  // Text layer: invisible but selectable, so a reader can copy a figure
  // straight out of the rendered page — and it is what the highlight attaches to.
  const textContent = await page.getTextContent();
  const layer = document.createElement('div');
  layer.className = 'textLayer';

  const candidate = matchCandidate(excerpt, { isTable });
  const covered = new Set(locateSpans(textContent.items, candidate));

  textContent.items.forEach((item, i) => {
    if (!item.str.trim()) return;
    const span = document.createElement('span');
    span.textContent = item.str;
    const tx = pdfjs.Util.transform(viewport.transform, item.transform);
    const fontSize = Math.hypot(tx[2], tx[3]);
    span.style.left = `${tx[4]}px`;
    span.style.top = `${tx[5] - fontSize}px`;
    span.style.fontSize = `${fontSize}px`;
    span.style.fontFamily = item.fontName;
    if (covered.has(i)) span.classList.add('mark');
    layer.appendChild(span);
  });

  wrap.appendChild(layer);
  setHighlightState(covered.size > 0, isTable);
  return { wrap, located: covered.size > 0 };
}

/* ── Panel ─────────────────────────────────────────────────────────────────── */

/**
 * Open the panel at a page.
 *
 * `source` is the SourceCitation the citation was built from; its excerpt is
 * what gets located on the page.
 */
export async function openSource({ docId, page, docName, excerpt, isTable, trigger }) {
  const panel = el('panel');
  const body = el('panelBody');
  const task = ++currentTask;

  lastTrigger = trigger || document.activeElement;

  panel.hidden = false;
  el('panelDoc').textContent = docName || docId;
  el('panelPage').textContent = `p.${page}`;
  el('panelDownload').href = documentFileUrl(docId);
  el('panelExcerpt').textContent = excerpt || '—';
  el('panelHlState').hidden = true;
  body.innerHTML = '<div class="panel-state">'
    + '<span class="spinner" role="status" aria-label="Loading page"></span></div>';

  try {
    const pdf = await loadDocument(docId);
    if (task !== currentTask) return;          // a newer citation superseded this

    if (page < 1 || page > pdf.numPages) {
      body.innerHTML = `<div class="panel-state">Page ${escapeHtml(page)} is outside `
        + `this document (${pdf.numPages} pages).</div>`;
      return;
    }
    el('panelPage').textContent = `p.${page} / ${pdf.numPages}`;

    const { wrap } = await renderPage(pdf, page, { excerpt, isTable });
    if (task !== currentTask) return;

    body.innerHTML = '';
    body.appendChild(wrap);

    const mark = wrap.querySelector('.mark');
    if (mark) mark.scrollIntoView({ block: 'center', behavior: 'instant' });
  } catch (error) {
    if (task !== currentTask) return;
    // Most likely a document ingested before PDFs were persisted. Say what it
    // is rather than showing an empty panel.
    body.innerHTML = '<div class="panel-state">Could not open this document.<br/>'
      + `${escapeHtml(error.message || 'Unknown error')}</div>`;
    el('panelHlState').hidden = true;
  }
}

export function closeSource() {
  el('panel').hidden = true;
  currentTask += 1;
  // Focus returns to the citation that opened the panel, so keyboard users are
  // not dropped back at the top of the document.
  if (lastTrigger && document.contains(lastTrigger)) lastTrigger.focus();
  lastTrigger = null;
}

export function initViewer() {
  el('panelClose').addEventListener('click', closeSource);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el('panel').hidden) {
      event.preventDefault();
      closeSource();
    }
  });
}

// Exported for unit tests.
export const _internal = { normalise, MIN_MATCH_CHARS };
