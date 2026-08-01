/**
 * Wiring and init.
 *
 * The only module with side effects — everything else exports functions. Keeps
 * the boot order in one readable place.
 */

import * as api from './api.js';
import { basisLabel, escapeHtml, formatCount } from './format.js';
import { toast } from './ui.js';

const el = (id) => document.getElementById(id);

/** Documents currently indexed, keyed by doc_id. Read by the viewer and chips. */
export const state = {
  documents: new Map(),
  docFilter: null,     // doc_name to restrict retrieval to, or null
  busy: false,
};

// ── Health ────────────────────────────────────────────────────────────────────

async function refreshHealth() {
  const dot = el('healthDot');
  const text = el('healthText');
  try {
    const h = await api.health();
    dot.className = 'dot ok';
    text.textContent = 'Service ready';
    // The model name is deliberately not shown here. It lives in the About
    // panel; a client should not read a third-party vendor in the chrome.
    state.health = h;
    el('statChunks').textContent = formatCount(h.documents_indexed);
  } catch (e) {
    dot.className = 'dot bad';
    text.textContent = e.status === 0 ? 'Service unreachable' : 'Service error';
  }
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
  { q: 'What was revenue?', trap: 'Unqualified on purpose — must not pick one silently' },
  { q: "What was Wipro's revenue in FY2025?", trap: 'Outside the corpus — must abstain' },
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

/**
 * Placeholder until the finding/abstention renderers land. Deliberately loud
 * rather than silent, so a half-wired build is obvious rather than looking like
 * a query that returned nothing.
 */
async function submitQuery() {
  const question = el('queryInput').value.trim();
  if (!question || state.busy) return;
  toast('Answer rendering is not wired up yet.', 'error');
  console.warn('submitQuery: awaiting ui.renderFinding', { question });
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
    // The one place the backend stack is named — for debugging, not for clients.
    toast(
      `Embedding: ${h.embedding_model} · Reranker: ${h.reranker_model} · `
      + `Store: ${h.vector_store_backend} · Generation: `
      + `${h.generation_available ? 'available' : 'unavailable (extractive only)'}`,
    );
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────

function init() {
  initComposer();
  initUpload();
  initAbout();
  refreshHealth();
  refreshDocuments();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

export { basisLabel, refreshDocuments, refreshHealth };
