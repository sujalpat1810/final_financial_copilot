/**
 * Shared rendering helpers.
 *
 * The finding, abstention and evidence renderers land here next; for now this
 * holds the pieces the shell already needs.
 */

import { escapeHtml } from './format.js';

const ICONS = {
  info: 'i-info',
  success: 'i-check',
  error: 'i-warn',
};

/**
 * Transient message.
 *
 * Errors persist until dismissed — a failed ingest or an unreachable service is
 * something the operator has to act on, and auto-hiding it after five seconds
 * means they can miss the only explanation they were given.
 */
export function toast(message, kind = 'info') {
  const host = document.getElementById('toasts');
  if (!host) return null;

  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  node.innerHTML = `
    <svg class="i i-sm"><use href="#${ICONS[kind] || ICONS.info}"/></svg>
    <span>${escapeHtml(message)}</span>`;

  if (kind === 'error') {
    const dismiss = document.createElement('button');
    dismiss.className = 'btn';
    dismiss.style.marginLeft = 'auto';
    dismiss.setAttribute('aria-label', 'Dismiss');
    dismiss.innerHTML = '<svg class="i i-sm"><use href="#i-x"/></svg>';
    dismiss.addEventListener('click', () => node.remove());
    node.appendChild(dismiss);
  } else {
    setTimeout(() => node.remove(), 4500);
  }

  host.appendChild(node);
  return node;
}

/** Remove the welcome panel the first time a real answer is rendered. */
export function hideWelcome() {
  const welcome = document.getElementById('welcome');
  if (welcome) welcome.remove();
}

/** Scroll the stream to the newest content. */
export function scrollToLatest() {
  const stream = document.getElementById('stream');
  if (stream) requestAnimationFrame(() => { stream.scrollTop = stream.scrollHeight; });
}
