/**
 * Wiring and init.
 *
 * The only module with side effects — everything else exports functions. Keeps
 * the boot order in one readable place.
 */

import * as api from './api.js';
import { onCitationActivate } from './citations.js';
import { basisLabel, escapeHtml, formatCount } from './format.js';
import { renderError, renderPending, renderResponse } from './render.js';
import { hideWelcome, scrollToLatest, toast } from './ui.js';
import { initViewer, openSource } from './viewer.js';

const el = (id) => document.getElementById(id);

/** Documents currently indexed, keyed by doc_id. Read by the viewer and chips. */
export const state = {
  documents: new Map(),
  // Every source ever rendered, so a citation clicked in an older answer can
  // still find the excerpt the viewer needs to locate on the page.
  sourcesByChunkId: new Map(),
  docFilter: null,     // doc_name to restrict retrieval to, or null
  busy: false,
};

// ── Health ────────────────────────────────────────────────────────────────────

async function refreshHealth() {
  const dot = el('healthDot');
  const text = el('healthText');
  try {
    const h = await api.health();
    const recovered = !state.health;
    dot.className = 'dot ok';
    text.textContent = 'Service ready';
    // The model name is deliberately not shown here. It lives in the About
    // panel; a client should not read a third-party vendor in the chrome.
    state.health = h;
    el('statChunks').textContent = formatCount(h.documents_indexed);
    // The corpus list is fetched once at boot. If that happened while the
    // service was still starting, it came back empty and never retried.
    if (recovered) refreshDocuments().catch(() => {});
    return true;
  } catch (e) {
    state.health = null;
    dot.className = 'dot bad';
    text.textContent = e.status === 0 ? 'Starting up…' : 'Service error';
    return false;
  }
}

/**
 * Keep asking until the service answers, then keep an eye on it.
 *
 * Health used to be fetched exactly once, at page load. Loading the app during
 * the ~34 s model warm-up therefore pinned "Service unreachable" in the header
 * for the rest of the session, with no retry and no button — the page looked
 * broken while the service behind it came up perfectly.
 *
 * Fast retries while it is down so a demo recovers on its own; slow polling once
 * it is up, purely to notice if it goes away.
 */
function watchHealth() {
  const DOWN_MS = 2000;
  const UP_MS = 30000;
  let timer = null;

  const tick = async () => {
    const ok = await refreshHealth();
    clearTimeout(timer);
    timer = setTimeout(tick, ok ? UP_MS : DOWN_MS);
  };
  tick();

  // No point polling a hidden tab; re-check the moment it comes back.
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) tick();
  });
}

// ── Documents ─────────────────────────────────────────────────────────────────

async function refreshDocuments() {
  let payload;
  try {
    payload = await api.listDocuments();
  } catch (e) {
    toast(e.message, 'error');
    return;
  }

  state.documents = new Map(payload.documents.map((d) => [d.doc_id, d]));
  el('statDocs').textContent = formatCount(payload.total);
  renderDocumentList(payload.documents);

  // Seeds are only useful once something is indexed; before that the welcome
  // text guides to upload instead.
  const hasDocs = payload.total > 0;
  el('seeds').hidden = !hasDocs;
  if (hasDocs) renderSeeds();
}

function renderDocumentList(documents) {
  const list = el('docList');

  if (!documents.length) {
    list.innerHTML =
      '<div class="doc-empty">No documents indexed yet.<br/>'
      + 'Add a PDF with its entity and fiscal year.</div>';
    return;
  }

  list.innerHTML = documents.map((d) => {
    const detected = d.standalone_pages + d.consolidated_pages;
    // A document with no detected basis will qualify every figure it produces
    // as undetermined. Say so here rather than letting it surprise the reader
    // inside an answer.
    const basisNote = detected === 0
      ? '<span class="doc-warn">no basis detected</span>'
      : `<span>${formatCount(d.standalone_pages)} SA · `
        + `${formatCount(d.consolidated_pages)} CO</span>`;
    const noFile = d.has_file ? '' : '<span class="doc-warn">no PDF</span>';

    return `
      <button class="doc" data-doc-id="${escapeHtml(d.doc_id)}"
              data-doc-name="${escapeHtml(d.doc_name)}"
              aria-pressed="${state.docFilter === d.doc_name}">
        <div class="doc-n">${escapeHtml(d.doc_name)}</div>
        <div class="doc-m">
          <span>${escapeHtml(d.entity || '—')}</span>
          <span>${escapeHtml(d.fiscal_year || '—')}</span>
        </div>
        <div class="doc-m">
          <span class="num">${formatCount(d.pages)}</span> pp
          <span class="num">${formatCount(d.chunks)}</span> chunks
          ${basisNote} ${noFile}
        </div>
      </button>`;
  }).join('');

  list.querySelectorAll('.doc').forEach((button) => {
    button.addEventListener('click', () => toggleDocFilter(button.dataset.docName));
  });
}

function toggleDocFilter(docName) {
  state.docFilter = state.docFilter === docName ? null : docName;
  renderDocumentList([...state.documents.values()]);
  toast(state.docFilter
    ? `Restricted to ${state.docFilter}.`
    : 'Searching all documents.');
}

// ── Seed questions ────────────────────────────────────────────────────────────

/**
 * Tuned to the demo corpus. The last two are the interesting ones: an
 * unqualified question that must not be silently resolved, and one outside the
 * corpus that must abstain. Those say more about trustworthiness than five
 * correct lookups.
 */
const SEEDS = [
  { q: 'What was Infosys consolidated revenue in FY2024-25?' },
  { q: 'How did Infosys revenue change from FY2024-25 to FY2025-26?' },
  { q: 'Compare Infosys and TCS revenue for FY2024-25' },
  { q: 'Who audited Infosys and was the opinion unqualified?' },
  { q: 'What contingent liabilities are disclosed?' },
  { q: 'List related party transactions' },
  // The two below are marked so they read as deliberate rather than careless.
  // Their tooltips describe the question from the reader's side — the earlier
  // wording ("must not pick one silently", "must abstain") was a note to
  // ourselves about expected behaviour, and read as backstage crib notes to
  // anyone hovering them in front of a client.
  { q: 'What was revenue?', trap: 'Ambiguous on purpose — no company, year or basis given' },
  { q: "What was Wipro's revenue in FY2025?", trap: 'A company outside the indexed corpus' },
];

function renderSeeds() {
  const seeds = el('seeds');
  seeds.innerHTML = SEEDS.map((s) => `
    <button class="seed${s.trap ? ' trap' : ''}"
            ${s.trap ? `title="${escapeHtml(s.trap)}"` : ''}>${escapeHtml(s.q)}</button>
  `).join('');

  seeds.querySelectorAll('.seed').forEach((button, i) => {
    button.addEventListener('click', () => {
      const input = el('queryInput');
      input.value = SEEDS[i].q;
      resizeInput();
      updateAskEnabled();
      input.focus();
    });
  });
}

// ── Composer ──────────────────────────────────────────────────────────────────

function resizeInput() {
  const input = el('queryInput');
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
}

function updateAskEnabled() {
  el('askBtn').disabled = state.busy || !el('queryInput').value.trim();
}

function initComposer() {
  const input = el('queryInput');

  input.addEventListener('input', () => {
    resizeInput();
    updateAskEnabled();
  });

  input.addEventListener('keydown', (event) => {
    // Enter submits, Shift+Enter adds a line. Multi-line questions are rare but
    // legitimate when quoting a clause.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submitQuery();
    }
  });

  el('askBtn').addEventListener('click', submitQuery);
}

async function submitQuery() {
  const input = el('queryInput');
  const question = input.value.trim();
  if (!question || state.busy) return;

  state.busy = true;
  updateAskEnabled();
  hideWelcome();

  const stream = el('streamInner');
  // renderPending drives a stage/elapsed ticker, so it hands back a stop() that
  // must run on every exit path — an interval left running after the card is
  // removed keeps firing against detached nodes for the life of the page.
  const { node: pending, stop: stopPending, showEvidence } =
    renderPending(stream, question);
  scrollToLatest();

  // Clear immediately: the question is already on screen as a heading, so
  // leaving it in the box invites an accidental duplicate submission.
  input.value = '';
  resizeInput();

  // Remember sources as soon as they arrive, so a citation clicked while the
  // prose is still being written can still find its excerpt.
  const rememberSources = (sources) => {
    for (const source of sources || []) {
      if (source.chunk_id) state.sourcesByChunkId.set(source.chunk_id, source);
    }
  };

  try {
    let meta = null;
    let final = null;
    let abstained = null;

    try {
      await api.queryStream({ question, docName: state.docFilter }, {
        onMeta: (m) => {
          meta = m;
          rememberSources(m.sources);
          // Retrieval is done ~25 s before the prose. Put the confidence and the
          // evidence on screen now rather than holding everything back.
          showEvidence(m, openableDocIds());
          scrollToLatest();
        },
        onAbstained: (a) => { abstained = a; rememberSources(a.sources); },
        onDone: (d) => { final = d; },
      });
    } catch (streamError) {
      // Streaming is an optimisation, not a requirement: an old build, a proxy
      // that buffers, or a dropped connection should cost the reader a slower
      // answer, not an error. Fall back to the blocking endpoint once.
      console.warn('[query] streaming failed, falling back', streamError);
      const response = await api.query({ question, docName: state.docFilter });
      stopPending();
      pending.remove();
      rememberSources(response.sources);
      renderResponse(stream, response, { openableDocIds: openableDocIds() });
      return;
    }

    stopPending();
    pending.remove();

    if (abstained) {
      renderResponse(stream, {
        question,
        answer: '',
        sources: abstained.sources,
        retrieval_latency_ms: abstained.retrieval_latency_ms,
        generation_latency_ms: 0,
        total_latency_ms: abstained.retrieval_latency_ms,
        answer_source: 'none',
        confidence: abstained.confidence,
        confidence_reason: abstained.confidence_reason,
        abstained: true,
        abstention_reason: abstained.abstention_reason,
        documents_searched: abstained.documents_searched,
        chunks_searched: abstained.chunks_searched,
      }, { openableDocIds: openableDocIds() });
    } else if (meta && final) {
      // Re-render from the complete text rather than from what was streamed:
      // "[Page 30]" can straddle two fragments, and citation chips can only be
      // built once the marker is whole.
      renderResponse(stream, {
        question,
        answer: final.answer,
        sources: meta.sources,
        retrieval_latency_ms: meta.retrieval_latency_ms,
        generation_latency_ms: final.generation_latency_ms,
        total_latency_ms: final.total_latency_ms,
        answer_source: final.answer_source,
        confidence: meta.confidence,
        confidence_reason: meta.confidence_reason,
        abstained: false,
        documents_searched: meta.documents_searched,
        chunks_searched: meta.chunks_searched,
      }, { openableDocIds: openableDocIds() });
    } else {
      renderError(stream, question, 'The answer ended before it was complete.');
    }
  } catch (e) {
    stopPending();
    pending.remove();
    renderError(stream, question, e.message);
  } finally {
    stopPending();          // belt and braces: never leave the ticker running
    state.busy = false;
    updateAskEnabled();
    scrollToLatest();
    input.focus();
  }
}

/**
 * Documents whose PDF is on disk and can therefore be opened from a citation.
 *
 * Derived from /documents rather than assumed: anything ingested before PDFs
 * were persisted has no file, and offering a link that 404s is worse than
 * rendering the citation inert.
 */
function openableDocIds() {
  return new Set(
    [...state.documents.values()].filter((d) => d.has_file).map((d) => d.doc_id),
  );
}

// ── Upload ────────────────────────────────────────────────────────────────────

function initUpload() {
  const zone = el('dropZone');
  const input = el('fileInput');

  input.addEventListener('change', () => {
    if (input.files.length) startUpload(input.files[0]);
  });

  // Drag and drop, kept from the previous UI.
  ['dragenter', 'dragover'].forEach((type) => {
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.add('over');
    });
  });
  ['dragleave', 'drop'].forEach((type) => {
    zone.addEventListener(type, (event) => {
      event.preventDefault();
      zone.classList.remove('over');
    });
  });
  zone.addEventListener('drop', (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) startUpload(file);
  });
}

async function startUpload(file) {
  const entity = el('entityInput').value.trim();
  const fiscalYear = el('fyInput').value.trim();

  // Required because they are never detected: an annual report is full of
  // comparative columns, so reading a year off the page is guessing, and a
  // figure attributed to the wrong company or year is the failure this tool
  // exists to prevent. Refuse rather than ingest something unattributable.
  const missing = [];
  if (!entity) missing.push('entityInput');
  if (!fiscalYear) missing.push('fyInput');
  ['entityInput', 'fyInput'].forEach((id) => {
    el(id).setAttribute('aria-invalid', String(missing.includes(id)));
  });
  if (missing.length) {
    toast('Entity and fiscal year are required — they are not read from the document.', 'error');
    el(missing[0]).focus();
    return;
  }

  if (!file.name.toLowerCase().endsWith('.pdf')) {
    toast('Only PDF files can be indexed.', 'error');
    return;
  }

  const progress = el('uploadProgress');
  const fill = el('progressFill');
  const label = el('progressLabel');
  progress.hidden = false;
  fill.classList.remove('pending');
  fill.style.width = '0%';
  label.textContent = `Uploading ${file.name}`;

  try {
    const result = await api.ingest({
      file,
      entity,
      fiscalYear,
      docName: el('docNameInput').value.trim(),
      onProgress: (fraction) => {
        fill.style.width = `${Math.round(fraction * 100)}%`;
        if (fraction >= 1) {
          // Transfer finished; the server is now parsing and embedding, which
          // reports nothing. An indeterminate bar is honest where a creeping
          // percentage would be invented.
          fill.style.width = '';
          fill.classList.add('pending');
          label.textContent = 'Parsing and embedding — this takes minutes for a large report';
        }
      },
    });

    toast(`Indexed ${result.doc_name}: ${formatCount(result.chunks_created)} chunks `
          + `from ${formatCount(result.pages_processed)} pages.`, 'success');
    el('fileInput').value = '';
    el('docNameInput').value = '';
    await Promise.all([refreshDocuments(), refreshHealth()]);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    progress.hidden = true;
    fill.classList.remove('pending');
  }
}

// ── About ─────────────────────────────────────────────────────────────────────

function initAbout() {
  el('aboutBtn').addEventListener('click', () => {
    const h = state.health;
    if (!h) {
      toast('Service details unavailable.', 'error');
      return;
    }
    // Model names go to the console, not the screen. This button used to print
    // the embedding, reranker and vector-store names into a toast — the one
    // place the app named its stack, and one mis-click away from projecting
    // third-party vendors to a room the rest of the UI works to keep them from.
    // The debugging value is preserved; only the audience changed.
    toast(
      `${formatCount(state.documents.size)} documents · `
      + `${formatCount(h.documents_indexed)} passages indexed · `
      + `answers ${h.generation_available ? 'are written from the evidence'
        : 'are quoted verbatim (generation unavailable)'}`,
    );
    console.info('[service]', {
      embedding: h.embedding_model,
      reranker: h.reranker_model,
      store: h.vector_store_backend,
      generation: h.generation_available,
    });
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────

function initCitations() {
  initViewer();
  // One delegated listener on the stream, so cards rendered later need no wiring.
  onCitationActivate(el('stream'), ({ docId, page, chunkId, trigger }) => {
    const doc = state.documents.get(docId);
    const source = state.sourcesByChunkId.get(chunkId);
    openSource({
      docId,
      page,
      docName: doc ? doc.doc_name : docId,
      // The excerpt is what gets located on the page. Without it the viewer
      // still opens, just without a highlight.
      excerpt: source ? source.excerpt : null,
      isTable: source ? source.is_table : true,
      trigger,
    });
  });
}

function init() {
  initComposer();
  initUpload();
  initAbout();
  initCitations();
  // watchHealth polls until the service answers, so opening the page mid-startup
  // recovers on its own instead of showing a stuck error.
  watchHealth();
  refreshDocuments();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

export { basisLabel, refreshDocuments, refreshHealth };
