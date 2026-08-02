/**
 * Turning a /query response into a finding or an insufficient-evidence card.
 *
 * The shape is deliberate. The question sits above as a quiet heading, the
 * answer is the primary element, and the evidence is attached below it and open
 * by default — not behind an accordion. For accountants and lawyers the evidence
 * IS the answer; hiding it by default would invert the point of the tool.
 *
 * Nothing here is a chat bubble.
 */

import { chipHtml, linkCitations } from './citations.js';
import { confidenceState, relevanceIsWeak } from './confidence.js';
import {
  escapeHtml,
  formatCount,
  formatMs,
  normaliseNumbersInText,
} from './format.js';

function icon(name, size = 'i-sm') {
  return `<svg class="i ${size}"><use href="#${name}"/></svg>`;
}

/* ── Answer prose ─────────────────────────────────────────────────────────────
   A deliberately small markdown subset: paragraphs, bullets, bold, and tables.
   Tables matter because the prompt asks for one when a question is unqualified
   and several answers are valid — rendering that as a wall of prose would lose
   the whole point of not silently picking one.

   Escaping happens first, so every transform below operates on inert text. */

function renderInline(text) {
  return text
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function isTableRow(line) {
  return line.trim().startsWith('|') && line.trim().endsWith('|');
}

function isTableDivider(line) {
  return /^\s*\|[\s:|-]+\|\s*$/.test(line);
}

function splitRow(line) {
  return line.trim().slice(1, -1).split('|').map((cell) => cell.trim());
}

/** A cell is numeric if it is only digits, separators and currency marks. */
function isNumericCell(cell) {
  return /^[₹\s]*-?[\d,.]+\s*(crore|lakh|%)?$/i.test(cell) && /\d/.test(cell);
}

function renderTable(rows) {
  const [header, ...body] = rows;
  const numericColumn = header.map((_, i) =>
    body.length > 0 && body.every((row) => !row[i] || isNumericCell(row[i])));

  const head = header
    .map((cell, i) => `<th${numericColumn[i] ? ' class="r"' : ''}>${renderInline(cell)}</th>`)
    .join('');
  const bodyHtml = body.map((row) => {
    const cells = row.map((cell, i) => {
      // Right-align numerics and set them in tabular figures, as any financial
      // statement would.
      const cls = numericColumn[i] ? ' class="num r"' : '';
      const marked = /not determined|unknown/i.test(cell)
        ? `<span class="bas-unknown">${renderInline(cell)}</span>`
        : renderInline(cell);
      return `<td${cls}>${marked}</td>`;
    }).join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  return `<table class="ftab"><thead><tr>${head}</tr></thead><tbody>${bodyHtml}</tbody></table>`;
}

function renderAnswerBody(answer) {
  // Normalise digit grouping before escaping: the corpus mixes Western and
  // Indian conventions, so raw model output does too.
  const escaped = escapeHtml(normaliseNumbersInText(answer));
  const lines = escaped.split('\n');
  const out = [];

  let paragraph = [];
  let bullets = [];
  let table = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${renderInline(paragraph.join(' '))}</p>`);
      paragraph = [];
    }
  };
  const flushBullets = () => {
    if (bullets.length) {
      out.push(`<ul>${bullets.map((b) => `<li>${renderInline(b)}</li>`).join('')}</ul>`);
      bullets = [];
    }
  };
  const flushTable = () => {
    if (table.length) {
      out.push(renderTable(table));
      table = [];
    }
  };
  const flushAll = () => { flushParagraph(); flushBullets(); flushTable(); };

  for (const line of lines) {
    const trimmed = line.trim();

    if (isTableRow(trimmed)) {
      flushParagraph();
      flushBullets();
      if (!isTableDivider(trimmed)) table.push(splitRow(trimmed));
      continue;
    }
    flushTable();

    if (!trimmed) { flushParagraph(); flushBullets(); continue; }

    const bullet = trimmed.match(/^[-*•]\s+(.*)$/);
    if (bullet) { flushParagraph(); bullets.push(bullet[1]); continue; }

    flushBullets();
    paragraph.push(trimmed);
  }
  flushAll();

  return out.join('');
}

/* ── Evidence ──────────────────────────────────────────────────────────────── */

function evidenceRow(source, openable) {
  const canOpen = openable && Boolean(source.doc_id);
  const weak = relevanceIsWeak(source.relevance) ? ' weak' : '';
  const tableFlag = source.is_table ? '<span class="tbl">TABLE</span>' : '';

  return `
    <button class="src${canOpen ? '' : ' inert'}" type="button"
            ${canOpen ? `data-doc-id="${escapeHtml(source.doc_id)}"
                         data-page="${escapeHtml(source.page_number)}"` : ''}
            ${source.chunk_id ? `data-chunk-id="${escapeHtml(source.chunk_id)}"` : ''}
            ${canOpen ? '' : 'disabled aria-disabled="true"'}>
      <span class="src-t">
        ${icon('i-file')}
        <span class="n">${escapeHtml(source.doc_name)}</span>
        <span class="pg">p.${escapeHtml(source.page_number)}</span>
        ${tableFlag}
        <span class="rel">
          <span class="rel-track">
            <span class="rel-fill${weak}" style="width:${Math.max(2, source.relevance)}%"></span>
          </span>
          <span class="rel-v">${source.relevance}</span>
        </span>
      </span>
      <span class="src-x${source.is_table ? ' mono' : ''}">${escapeHtml(source.excerpt)}</span>
    </button>`;
}

function evidenceSection(response, openableDocIds) {
  if (!response.sources.length) return '';

  const rows = response.sources
    .map((s) => evidenceRow(s, !openableDocIds || openableDocIds.has(s.doc_id)))
    .join('');

  // Latency stays visible. Real numbers build trust with a sceptical reader and
  // cost nothing to show.
  const stats = `
    <span class="ev-stats">
      <span>${icon('i-zap')} retrieval <b class="v">${formatMs(response.retrieval_latency_ms)}</b> ms</span>
      <span>${icon('i-spark')} generation <b class="v">${formatMs(response.generation_latency_ms)}</b> ms</span>
      <span>${icon('i-clock')} total <b class="v">${formatMs(response.total_latency_ms)}</b> ms</span>
    </span>`;

  const count = response.sources.length;
  return `
    <div class="ev">
      <div class="ev-hd">
        ${icon('i-quote')}
        <span class="lbl">Evidence · ${count} source${count === 1 ? '' : 's'}</span>
        ${stats}
      </div>
      ${rows}
    </div>`;
}

/* ── Cards ─────────────────────────────────────────────────────────────────── */

function questionHeading(question) {
  return `
    <div class="qline">
      <span class="lbl">Q</span>
      <h2 class="q">${escapeHtml(question)}</h2>
    </div>`;
}

function findingCard(response, openableDocIds) {
  const state = confidenceState(response.confidence);
  const body = renderAnswerBody(response.answer);
  const linked = linkCitations(body, response.sources, { openableDocIds });

  // "Generated" vs "Extractive" — what matters to the reader is whether the
  // answer was synthesised or quoted, not which vendor produced it.
  const mode = response.answer_source === 'generated' ? 'Generated' : 'Extractive';

  return `
    <article class="card">
      <div class="card-hd">
        <span class="lbl">Finding</span>
        <span class="conf ${state.className}" title="${escapeHtml(state.description)}">
          ${icon(state.icon)} ${escapeHtml(state.label)}
        </span>
        <span class="conf-why">${escapeHtml(response.confidence_reason || '')}</span>
        <span class="hspacer" style="flex:1"></span>
        <span class="lbl">${mode}</span>
      </div>
      <div class="card-bd"><div class="ans">${linked}</div></div>
      ${evidenceSection(response, openableDocIds)}
    </article>`;
}

function abstentionCard(response, openableDocIds) {
  // Near-misses, shown with their relevance. Reporting what was searched and
  // what nearly matched turns a non-answer into useful information — and for a
  // liability-conscious reader, a system that knows its limits is more
  // persuasive than one that always answers.
  const near = response.sources.length
    ? `<div class="near">
         <span class="lbl">Closest matches — all below the confidence threshold</span>
         ${response.sources.map((s) => {
           const canOpen = (!openableDocIds || openableDocIds.has(s.doc_id)) && s.doc_id;
           return `
             <button class="near-row${canOpen ? '' : ' inert'}" type="button"
                     ${canOpen ? `data-doc-id="${escapeHtml(s.doc_id)}"
                                  data-page="${escapeHtml(s.page_number)}"` : 'disabled'}>
               ${icon('i-file')}
               <span class="n">${escapeHtml(s.doc_name)}${
                 s.section_title ? ` — ${escapeHtml(s.section_title)}` : ''}</span>
               <span class="pg">p.${escapeHtml(s.page_number)}</span>
               <span class="rel">
                 <span class="rel-track">
                   <span class="rel-fill weak" style="width:${Math.max(2, s.relevance)}%"></span>
                 </span>
                 <span class="rel-v">${s.relevance}</span>
               </span>
             </button>`;
         }).join('')}
       </div>`
    : '';

  return `
    <article class="card abstained">
      <div class="card-hd">
        ${icon('i-shield')}
        <span class="lbl">Insufficient evidence</span>
        <span class="conf-why">${escapeHtml(response.confidence_reason || '')}</span>
      </div>
      <div class="card-bd">
        <p class="abst-msg">${escapeHtml(
          response.abstention_reason
          || "The indexed documents don't contain enough information to answer this reliably.",
        )}</p>
        <div class="abst-scope">
          <div>
            <span class="lbl">Documents searched</span>
            <span class="v">${formatCount(response.documents_searched)}</span>
          </div>
          <div>
            <span class="lbl">Chunks searched</span>
            <span class="v">${formatCount(response.chunks_searched)}</span>
          </div>
          <div>
            <span class="lbl">Answer generated</span>
            <span class="v text">No</span>
          </div>
        </div>
        ${near}
      </div>
    </article>`;
}

/**
 * Render one exchange into the stream.
 *
 * `openableDocIds` is the set of documents with a stored PDF. Citations for
 * anything else render inert rather than offering a link that 404s.
 */
export function renderResponse(container, response, { openableDocIds } = {}) {
  const node = document.createElement('div');
  node.className = 'exchange';
  node.innerHTML = questionHeading(response.question)
    + (response.abstained
      ? abstentionCard(response, openableDocIds)
      : findingCard(response, openableDocIds));
  container.appendChild(node);
  return node;
}

/** Placeholder shown while a query is in flight. */
export function renderPending(container, question) {
  const node = document.createElement('div');
  node.className = 'exchange';
  node.innerHTML = questionHeading(question) + `
    <article class="card">
      <div class="working">
        <span class="spinner" role="status" aria-label="Searching"></span>
        Searching the indexed documents…
      </div>
    </article>`;
  container.appendChild(node);
  return node;
}

export function renderError(container, question, message) {
  const node = document.createElement('div');
  node.className = 'exchange';
  node.innerHTML = questionHeading(question) + `
    <article class="card abstained">
      <div class="card-hd">
        ${icon('i-warn')}<span class="lbl">Request failed</span>
      </div>
      <div class="card-bd"><p class="abst-msg">${escapeHtml(message)}</p></div>
    </article>`;
  container.appendChild(node);
  return node;
}

// Exported for unit tests.
export const _internal = { renderAnswerBody, renderTable, isNumericCell };
