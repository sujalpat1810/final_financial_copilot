/**
 * The agent-activity trace — a vertical list of pipeline stages with live
 * state and real counts.
 *
 * This is the demo's "agentic system, not a chatbot" moment, and it is honest
 * by construction: every line is a stage event the backend actually emitted,
 * carrying counts the pipeline actually measured. Nothing here is animated
 * for effect or invented for pacing.
 *
 * Shared by the Reconcile and Notices panels — both stream the same
 * stage/done/error frame contract.
 */

import { escapeHtml, formatCount } from './format.js';

/** Human labels for stage keys, per pipeline. Unknown keys fall back as-is. */
const STAGE_LABELS = {
  load: 'Load inputs',
  match: 'Match records',
  classify: 'Classify exceptions',
  explain: 'Explain exceptions',
  extract: 'Read the notice',
  retrieve: 'Retrieve provisions',
  draft: 'Draft the reply',
};

function describeCounts(counts) {
  if (!counts) return '';
  return Object.entries(counts)
    .map(([key, value]) => `${formatCount(value)} ${key.replace(/_/g, ' ')}`)
    .join(' · ');
}

/**
 * Mount a trace into `container`. Returns handlers the stream loop calls:
 *   onStage(payload)  — {stage, state: running|done, counts?}
 *   done() / fail(message)
 */
export function createTrace(container) {
  container.innerHTML = '<ol class="trace" role="list"></ol>';
  const list = container.querySelector('.trace');
  const items = new Map();   // stage key -> li

  function upsert(stage, state, counts) {
    let li = items.get(stage);
    if (!li) {
      li = document.createElement('li');
      li.className = 'trace-step';
      list.appendChild(li);
      items.set(stage, li);
    }
    li.dataset.state = state;
    const label = STAGE_LABELS[stage] || stage;
    const detail = describeCounts(counts);
    li.innerHTML = `
      <span class="trace-marker" aria-hidden="true"></span>
      <span class="trace-label">${escapeHtml(label)}</span>
      ${detail ? `<span class="trace-detail num">${escapeHtml(detail)}</span>` : ''}
      <span class="visually-hidden">${state === 'done' ? 'complete' : 'in progress'}</span>`;
  }

  return {
    onStage(payload) {
      upsert(payload.stage, payload.state || 'running', payload.counts);
    },
    done() {
      // Anything still marked running when the terminal frame arrives did
      // finish — the backend just didn't emit a separate done for it.
      items.forEach((li) => { li.dataset.state = 'done'; });
    },
    fail(message) {
      const li = document.createElement('li');
      li.className = 'trace-step';
      li.dataset.state = 'error';
      li.innerHTML = `
        <span class="trace-marker" aria-hidden="true"></span>
        <span class="trace-label">${escapeHtml(message || 'Run failed')}</span>`;
      list.appendChild(li);
    },
  };
}
