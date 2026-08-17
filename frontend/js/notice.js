/**
 * The Notices workspace: pick a scrutiny notice, watch the casework pipeline
 * (extract → retrieve → draft), read the drafted reply with its citations,
 * then approve or send back — the human gate the whole feature exists for.
 *
 * The draft is always presented as a draft for CA review. There is no "send"
 * anywhere in this UI, deliberately: the demo's claim is "nothing leaves this
 * screen without a chartered accountant's sign-off", and the interface is the
 * proof.
 */

import * as api from './api.js';
import { chipHtml, linkCitations, onCitationActivate } from './citations.js';
import { escapeHtml, formatINR } from './format.js';
import { renderAnswerBody } from './render.js';
import { toast } from './ui.js';
import { createTrace } from './trace.js';
import { openSource } from './viewer.js';

const state = {
  initialised: false,
  running: false,
  notices: [],
  reply: null,
  sourcesByChunkId: new Map(),
};

const $ = (sel, root = document) => root.querySelector(sel);

function renderSkeleton(root) {
  root.innerHTML = `
    <section class="notices">
      <header class="recon-head">
        <h1>Notices</h1>
        <p class="recon-sub">
          Scrutiny-notice analysis: particulars are extracted from the notice,
          the governing provisions retrieved from the statute library, and a
          reply drafted for review. A draft never leaves without sign-off.
        </p>
      </header>

      <div class="recon-controls">
        <div class="field" style="flex:1">
          <label for="noticeSelect">Notice</label>
          <select id="noticeSelect"></select>
        </div>
        <button class="btn-primary" id="noticeAnalyze">
          <svg class="i i-sm"><use href="#i-zap"/></svg> Analyze notice
        </button>
      </div>

      <div class="recon-trace" id="noticeTrace" hidden></div>
      <div id="noticeDiscrepancy" hidden></div>
      <div id="noticeReply" hidden></div>
    </section>`;
}

// ── Notice list ───────────────────────────────────────────────────────────────

async function loadNotices() {
  const select = $('#noticeSelect');
  try {
    const body = await api.listNotices();
    state.notices = body.notices;
    if (!body.notices.length) {
      select.innerHTML = '<option value="">No notices indexed</option>';
      $('#noticeAnalyze').disabled = true;
      return;
    }
    select.innerHTML = body.notices.map((n) => `
      <option value="${escapeHtml(n.doc_id)}">
        ${escapeHtml(n.doc_name)}${n.latest_reply_status
          ? ` — reply ${escapeHtml(n.latest_reply_status)}` : ''}
      </option>`).join('');
    $('#noticeAnalyze').disabled = false;
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Analysis run ──────────────────────────────────────────────────────────────

async function analyze() {
  if (state.running) return;
  state.running = true;
  $('#noticeAnalyze').disabled = true;
  $('#noticeDiscrepancy').hidden = true;
  $('#noticeReply').hidden = true;

  const traceHost = $('#noticeTrace');
  traceHost.hidden = false;
  const trace = createTrace(traceHost);

  try {
    let reply = null;
    await api.analyzeNotice($('#noticeSelect').value, (name, payload) => {
      if (name === 'stage') trace.onStage(payload);
      else if (name === 'discrepancy') renderDiscrepancy(payload);
      else if (name === 'done') reply = payload.reply;
    });
    if (!reply) throw new Error('The analysis ended before it was complete.');
    trace.done();
    renderReply(reply);
    loadNotices();
  } catch (e) {
    trace.fail(e.message);
    toast(e.message, 'error');
  } finally {
    state.running = false;
    $('#noticeAnalyze').disabled = false;
  }
}

// ── Discrepancy card ──────────────────────────────────────────────────────────

function renderDiscrepancy(d) {
  const host = $('#noticeDiscrepancy');
  const row = (label, value) => (value === null || value === undefined || value === ''
    ? ''
    : `<div class="disc-row"><span class="disc-l">${escapeHtml(label)}</span>
       <span class="disc-v">${escapeHtml(String(value))}</span></div>`);
  host.innerHTML = `
    <div class="disc-card">
      <h3 class="drawer-h">Extracted particulars
        <span class="src-tag">[${d.extraction === 'generated' ? 'Model-read' : 'Read literally'}]</span>
      </h3>
      ${row('Notice', d.notice_type)}
      ${row('Reference', d.reference_no)}
      ${row('GSTIN', d.gstin)}
      ${row('Period', d.period)}
      ${row('Amount', d.amount != null ? `₹${formatINR(d.amount)}` : null)}
      ${row('Sections cited', (d.sections_cited || []).join(', '))}
      ${row('Reply form', d.reply_form)}
      ${row('Reply due', d.reply_due_days ? `${d.reply_due_days} days` : null)}
      ${row('Alleged', d.alleged_discrepancy)}
    </div>`;
  host.hidden = false;
}

// ── Draft reply ───────────────────────────────────────────────────────────────

function rememberSources(sources) {
  for (const s of sources || []) {
    if (s.chunk_id) state.sourcesByChunkId.set(s.chunk_id, s);
  }
}

function renderReply(reply) {
  state.reply = reply;
  rememberSources(reply.sources);
  const host = $('#noticeReply');
  const decided = reply.status === 'approved';

  const bodyHtml = linkCitations(
    renderAnswerBody(reply.draft_md), reply.sources, {});

  host.innerHTML = `
    <div class="reply-card">
      <div class="reply-head">
        <h3 class="drawer-h" style="margin:0">
          Drafted reply <span class="src-tag">[Draft for CA review]</span>
        </h3>
        <span class="status-chip s-${escapeHtml(reply.status)}">
          ${escapeHtml(reply.status === 'approved' ? 'Approved' : 'Draft')}
        </span>
      </div>
      <div class="reply-body">${bodyHtml}</div>
      <div class="reply-sources">
        <span class="lbl">Provisions relied on</span>
        <div class="reply-chips">
          ${(reply.sources || []).map((s) => chipHtml(s)).join('')}
        </div>
      </div>
      <div class="drawer-actions" ${decided ? 'hidden' : ''}>
        <label class="visually-hidden" for="replyNote">Decision note</label>
        <input id="replyNote" placeholder="Note (optional)" autocomplete="off" />
        <button class="btn-primary" data-reply-action="approve">Approve</button>
        <button class="btn" data-reply-action="request_changes">Request changes</button>
      </div>
      <p class="drawer-decided" ${decided ? '' : 'hidden'}>
        Approved by the reviewing CA — ready for filing. The draft is now
        immutable; changes require a new draft.
      </p>
    </div>`;
  host.hidden = false;

  host.querySelectorAll('[data-reply-action]').forEach((button) => {
    button.addEventListener('click', async () => {
      const note = $('#replyNote', host).value.trim();
      try {
        const updated = await api.decideReply(
          reply.reply_id, button.dataset.replyAction, note);
        toast(updated.status === 'approved'
          ? 'Reply approved and locked.'
          : 'Sent back for changes.', 'success');
        renderReply(updated);
        loadNotices();
      } catch (err) {
        toast(err.message, 'error');
      }
    });
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────

export function initNotices() {
  if (state.initialised) return;
  state.initialised = true;
  const root = document.getElementById('noticeRoot');
  renderSkeleton(root);
  loadNotices();
  $('#noticeAnalyze').addEventListener('click', analyze);

  // Citation chips in the draft open the statute PDF at the cited page.
  onCitationActivate(root, ({ docId, page, chunkId, trigger }) => {
    const source = state.sourcesByChunkId.get(chunkId);
    openSource({
      docId,
      page,
      docName: source ? source.doc_name : docId,
      excerpt: source ? source.excerpt : null,
      isTable: source ? source.is_table : true,
      trigger,
    });
  });
}

document.addEventListener('viewshown', (ev) => {
  if (ev.detail.view === 'notices') initNotices();
});
