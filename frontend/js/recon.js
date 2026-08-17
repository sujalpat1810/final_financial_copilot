/**
 * The Reconcile workspace: run the reconciliation agent, watch its trace,
 * review the exceptions queue, decide each one, export the run log.
 *
 * Design rules carried in from the research that shaped this demo:
 * - EXCEPTIONS-ONLY QUEUE: matched rows are a count, not a table. The
 *   reviewer's attention goes where judgment is needed.
 * - Verification badges are deterministic facts phrased as facts ("38/40
 *   GSTINs pass checksum"), never percentage-confidence vibes.
 * - Every decision is an explicit action recorded against the reviewer;
 *   nothing auto-clears.
 */

import * as api from './api.js';
import { escapeHtml, formatCount, formatINR } from './format.js';
import { toast } from './ui.js';
import { createTrace } from './trace.js';

const BUCKET_LABELS = {
  all: 'All',
  in_books_not_2b: 'In books, not 2B',
  in_2b_not_books: 'In 2B, not books',
  amount_mismatch: 'Amount mismatch',
  gstin_mismatch: 'GSTIN mismatch',
  duplicate_in_books: 'Duplicate',
};

const STATUS_LABELS = {
  open: 'Open', accepted: 'Accepted', corrected: 'Corrected', rejected: 'Rejected',
};

const state = {
  initialised: false,
  running: false,
  runId: null,
  stats: null,
  exceptions: [],
  bucket: 'all',
  openExcId: null,
};

const $ = (sel, root = document) => root.querySelector(sel);

// ── Panel skeleton ────────────────────────────────────────────────────────────

function renderSkeleton(root) {
  root.innerHTML = `
    <section class="recon">
      <header class="recon-head">
        <h1>Reconciliation</h1>
        <p class="recon-sub">
          Purchase register ↔ GSTR-2B. Matching is deterministic — the model
          explains exceptions, it never computes them.
        </p>
      </header>

      <div class="recon-controls">
        <div class="field">
          <label for="reconClient">Client</label>
          <select id="reconClient"></select>
        </div>
        <div class="field">
          <label for="reconPeriod">Period</label>
          <select id="reconPeriod">
            <option value="2025-12">December 2025</option>
          </select>
        </div>
        <button class="btn-primary" id="reconRun">
          <svg class="i i-sm"><use href="#i-zap"/></svg> Run reconciliation
        </button>
        <div style="flex:1"></div>
        <select id="reconPastRuns" title="Past runs" hidden></select>
        <a class="btn" id="reconLog" hidden download>
          <svg class="i i-sm"><use href="#i-dl"/></svg> Run log
        </a>
      </div>

      <div class="recon-trace" id="reconTrace" hidden></div>
      <div class="recon-badges" id="reconBadges" hidden></div>

      <div class="recon-results" id="reconResults" hidden>
        <nav class="bucket-tabs" id="bucketTabs" aria-label="Exception buckets"></nav>
        <div class="exc-table-wrap">
          <table class="exc-table" id="excTable">
            <thead>
              <tr>
                <th scope="col">Invoice</th><th scope="col">Vendor</th>
                <th scope="col" class="num-h">Books ₹</th>
                <th scope="col" class="num-h">GSTR-2B ₹</th>
                <th scope="col">Bucket</th><th scope="col">Status</th>
              </tr>
            </thead>
            <tbody></tbody>
          </table>
        </div>
        <div class="exc-drawer" id="excDrawer" hidden></div>
      </div>
    </section>`;
}

// ── Controls ──────────────────────────────────────────────────────────────────

async function loadClients() {
  const select = $('#reconClient');
  try {
    const body = await api.reconClients();
    select.innerHTML = body.clients.map((c) =>
      `<option value="${escapeHtml(c.client_id)}">${escapeHtml(c.name)}</option>`,
    ).join('');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function loadPastRuns() {
  const select = $('#reconPastRuns');
  try {
    const body = await api.reconRuns();
    const done = body.runs.filter((r) => r.status === 'done');
    select.hidden = done.length === 0;
    select.innerHTML = '<option value="">Past runs…</option>' + done.map((r) =>
      `<option value="${escapeHtml(r.run_id)}">${escapeHtml(r.period)} · `
      + `${escapeHtml(r.started_at.slice(0, 16).replace('T', ' '))}</option>`,
    ).join('');
  } catch { /* past runs are a convenience, not a requirement */ }
}

// ── Run ───────────────────────────────────────────────────────────────────────

async function runReconciliation() {
  if (state.running) return;
  state.running = true;
  $('#reconRun').disabled = true;
  $('#reconResults').hidden = true;
  $('#reconBadges').hidden = true;

  const traceHost = $('#reconTrace');
  traceHost.hidden = false;
  const trace = createTrace(traceHost);

  try {
    let doneStats = null;
    let runId = null;
    await api.runRecon({
      clientId: $('#reconClient').value,
      period: $('#reconPeriod').value,
    }, (name, payload) => {
      if (name === 'stage') {
        runId = payload.run_id;
        trace.onStage(payload);
      } else if (name === 'done') {
        runId = payload.run_id;
        doneStats = payload.stats;
      }
    });

    if (!doneStats) throw new Error('The run ended before it was complete.');
    trace.done();
    state.runId = runId;
    state.stats = doneStats;
    renderBadges(doneStats);
    await showExceptions(runId);
    $('#reconLog').href = api.reconLogUrl(runId);
    $('#reconLog').hidden = false;
    loadPastRuns();
  } catch (e) {
    trace.fail(e.message);
    toast(e.message, 'error');
  } finally {
    state.running = false;
    $('#reconRun').disabled = false;
  }
}

// ── Badges — deterministic verification facts ─────────────────────────────────

function renderBadges(stats) {
  const host = $('#reconBadges');
  const checks = stats.checks || {};
  const matched = formatCount(stats.exact_matches + (stats.amendment_resolved || 0));
  const badge = (ok, text) => `
    <span class="vbadge ${ok ? 'ok' : 'warn'}">
      <svg class="i i-sm"><use href="#i-${ok ? 'check' : 'warn'}"/></svg>
      ${escapeHtml(text)}
    </span>`;

  const gstinBooksOk = checks.gstin_valid_books === checks.gstin_total_books;
  const gstin2bOk = checks.gstin_valid_2b === checks.gstin_total_2b;
  const taxOk = checks.tax_split_ok === checks.tax_split_total;

  host.innerHTML = [
    badge(true, `${matched} records matched`),
    badge(gstinBooksOk,
      `${formatCount(checks.gstin_valid_books)}/${formatCount(checks.gstin_total_books)} books GSTINs pass checksum`),
    badge(gstin2bOk,
      `${formatCount(checks.gstin_valid_2b)}/${formatCount(checks.gstin_total_2b)} 2B GSTINs pass checksum`),
    badge(taxOk,
      `${formatCount(checks.tax_split_ok)}/${formatCount(checks.tax_split_total)} tax splits legal`),
    badge(false, `ITC at risk ₹${formatINR(stats.itc_at_risk)}`),
  ].join('');
  host.hidden = false;
}

// ── Exceptions queue ──────────────────────────────────────────────────────────

async function showExceptions(runId) {
  const body = await api.reconExceptions(runId);
  state.exceptions = body.exceptions;
  state.bucket = 'all';
  state.openExcId = null;
  renderBucketTabs();
  renderTable();
  $('#reconResults').hidden = false;
}

function bucketCounts() {
  const counts = { all: state.exceptions.length };
  for (const e of state.exceptions) {
    counts[e.bucket] = (counts[e.bucket] || 0) + 1;
  }
  return counts;
}

function renderBucketTabs() {
  const tabs = $('#bucketTabs');
  const counts = bucketCounts();
  tabs.innerHTML = Object.entries(BUCKET_LABELS)
    .filter(([key]) => key === 'all' || counts[key])
    .map(([key, label]) => `
      <button class="bucket-tab${state.bucket === key ? ' active' : ''}"
              data-bucket="${key}">
        ${escapeHtml(label)} <span class="num">${formatCount(counts[key] || 0)}</span>
      </button>`).join('');
  tabs.querySelectorAll('.bucket-tab').forEach((b) => {
    b.addEventListener('click', () => {
      state.bucket = b.dataset.bucket;
      state.openExcId = null;
      renderBucketTabs();
      renderTable();
    });
  });
}

function rowFields(e) {
  const b = e.books_row || {};
  const g = e.g2b_row || {};
  return {
    invoice: b.invoice_no || g.invoice_no || '—',
    vendor: b.vendor_name || g.trade_name || '—',
    booksValue: b.taxable_value,
    g2bValue: g.taxable_value,
  };
}

function renderTable() {
  const tbody = $('#excTable tbody');
  const visible = state.exceptions.filter(
    (e) => state.bucket === 'all' || e.bucket === state.bucket);

  tbody.innerHTML = visible.map((e) => {
    const f = rowFields(e);
    return `
      <tr data-exc-id="${e.exc_id}" class="${state.openExcId === e.exc_id ? 'open' : ''}"
          tabindex="0" role="button" aria-expanded="${state.openExcId === e.exc_id}">
        <td class="num">${escapeHtml(f.invoice)}</td>
        <td>${escapeHtml(f.vendor)}</td>
        <td class="num">${f.booksValue != null ? formatINR(f.booksValue) : '—'}</td>
        <td class="num">${f.g2bValue != null ? formatINR(f.g2bValue) : '—'}</td>
        <td><span class="bucket-chip b-${escapeHtml(e.bucket)}">${escapeHtml(BUCKET_LABELS[e.bucket] || e.bucket)}</span></td>
        <td><span class="status-chip s-${escapeHtml(e.status)}">${escapeHtml(STATUS_LABELS[e.status] || e.status)}</span></td>
      </tr>`;
  }).join('') || '<tr><td colspan="6" class="exc-empty">No exceptions in this bucket.</td></tr>';

  tbody.querySelectorAll('tr[data-exc-id]').forEach((tr) => {
    const open = () => toggleDrawer(Number(tr.dataset.excId));
    tr.addEventListener('click', open);
    tr.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });

  renderDrawer();
}

// ── Drawer — one exception, both rows, the explanation, the decision ──────────

function toggleDrawer(excId) {
  state.openExcId = state.openExcId === excId ? null : excId;
  renderTable();
}

function sideBySide(e) {
  const rows = [];
  const keys = ['invoice_no', 'invoice_date', 'vendor_name', 'trade_name',
    'vendor_gstin', 'supplier_gstin', 'taxable_value', 'cgst', 'sgst', 'igst'];
  const b = e.books_row || {};
  const g = e.g2b_row || {};
  const label = {
    invoice_no: 'Invoice', invoice_date: 'Date', vendor_name: 'Vendor',
    trade_name: 'Vendor', vendor_gstin: 'GSTIN', supplier_gstin: 'GSTIN',
    taxable_value: 'Taxable value', cgst: 'CGST', sgst: 'SGST', igst: 'IGST',
  };
  const seen = new Set();
  for (const key of keys) {
    const name = label[key];
    if (seen.has(name)) continue;
    const bKey = key in b ? key : { trade_name: 'vendor_name', supplier_gstin: 'vendor_gstin' }[key];
    const gKey = key in g ? key : { vendor_name: 'trade_name', vendor_gstin: 'supplier_gstin' }[key];
    const bVal = b[bKey];
    const gVal = g[gKey];
    if (bVal === undefined && gVal === undefined) continue;
    seen.add(name);
    const numeric = ['taxable_value', 'cgst', 'sgst', 'igst'].includes(key);
    const fmt = (v) => (v === undefined || v === null || v === ''
      ? '—' : (numeric ? formatINR(v) : String(v)));
    const differs = bVal !== undefined && gVal !== undefined
      && String(bVal) !== String(gVal);
    rows.push(`
      <tr class="${differs ? 'differs' : ''}">
        <th scope="row">${escapeHtml(name)}</th>
        <td class="${numeric ? 'num' : ''}">${escapeHtml(fmt(bVal))}</td>
        <td class="${numeric ? 'num' : ''}">${escapeHtml(fmt(gVal))}</td>
      </tr>`);
  }
  return rows.join('');
}

function renderDrawer() {
  const drawer = $('#excDrawer');
  const e = state.exceptions.find((x) => x.exc_id === state.openExcId);
  if (!e) { drawer.hidden = true; drawer.innerHTML = ''; return; }

  const modelTag = e.llm_model && e.llm_model !== 'deterministic'
    ? '<span class="src-tag">[Model-written]</span>'
    : '<span class="src-tag">[Computed]</span>';
  const decided = e.status !== 'open';

  drawer.innerHTML = `
    <div class="drawer-grid">
      <div>
        <h3 class="drawer-h">Books vs GSTR-2B</h3>
        <table class="cmp-table">
          <thead><tr><th></th><th scope="col">Books</th><th scope="col">GSTR-2B</th></tr></thead>
          <tbody>${sideBySide(e)}</tbody>
        </table>
      </div>
      <div>
        <h3 class="drawer-h">Explanation ${modelTag}</h3>
        <p class="drawer-explain">${escapeHtml(e.llm_explanation || '—')}</p>
        <div class="drawer-actions" ${decided ? 'hidden' : ''}>
          <label class="visually-hidden" for="decisionNote">Decision note</label>
          <input id="decisionNote" placeholder="Note (optional)" autocomplete="off" />
          <button class="btn-primary" data-action="accepted">Accept</button>
          <button class="btn" data-action="corrected">Correct</button>
          <button class="btn" data-action="rejected">Reject</button>
        </div>
        <p class="drawer-decided" ${decided ? '' : 'hidden'}>
          Decision recorded: <b>${escapeHtml(STATUS_LABELS[e.status] || e.status)}</b>
        </p>
      </div>
    </div>`;
  drawer.hidden = false;

  drawer.querySelectorAll('[data-action]').forEach((button) => {
    button.addEventListener('click', async () => {
      const note = $('#decisionNote', drawer).value.trim();
      try {
        const updated = await api.reconDecide(e.exc_id, button.dataset.action, note);
        const index = state.exceptions.findIndex((x) => x.exc_id === e.exc_id);
        if (index >= 0) state.exceptions[index] = updated;
        toast(`Recorded: ${STATUS_LABELS[updated.status] || updated.status}.`, 'success');
        renderTable();
      } catch (err) {
        toast(err.message, 'error');
      }
    });
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function loadRun(runId) {
  try {
    state.runId = runId;
    $('#reconTrace').hidden = true;
    const run = await api.reconRunDetail(runId);
    if (run?.stats && Object.keys(run.stats).length) {
      state.stats = run.stats;
      renderBadges(run.stats);
    }
  } catch { /* stats are decorative for an old run */ }
  await showExceptions(runId);
  $('#reconLog').href = api.reconLogUrl(runId);
  $('#reconLog').hidden = false;
}

export function initRecon() {
  if (state.initialised) return;
  state.initialised = true;
  const root = document.getElementById('reconRoot');
  renderSkeleton(root);
  loadClients();
  loadPastRuns();
  $('#reconRun').addEventListener('click', runReconciliation);
  $('#reconPastRuns').addEventListener('change', (ev) => {
    if (ev.target.value) loadRun(ev.target.value);
  });
}

document.addEventListener('viewshown', (ev) => {
  if (ev.detail.view === 'recon') initRecon();
});
