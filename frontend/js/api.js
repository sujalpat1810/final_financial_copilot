/**
 * Every network call, in one place.
 *
 * Same-origin: the frontend is served by the API at /app, so there is no base
 * URL to configure and no CORS to negotiate. The previous version hardcoded
 * http://localhost:8000, which broke the moment it was served from anywhere
 * else.
 */

/**
 * The page is served from <root>/app/, so the API root is one level up.
 *
 * Derived rather than hardcoded to "/" so the whole app still works behind a
 * reverse proxy on a path prefix — an on-prem install is exactly where that
 * happens. `new URL('./', href)` normalises away any filename first, so both
 * /app/ and /app/index.html resolve the same way.
 *
 * Resolved on first use rather than at module scope: touching window during
 * import makes this module — and every module that imports it — impossible to
 * load under `node --test`, which is where the viewer's matching logic is
 * tested.
 */
let apiRoot = null;

function root() {
  if (!apiRoot) {
    apiRoot = new URL('../', new URL('./', window.location.href));
  }
  return apiRoot;
}

function apiUrl(path) {
  return new URL(path.replace(/^\//, ''), root()).toString();
}

/**
 * A failed request carries the server's `detail` message where there is one.
 * FastAPI puts real explanations there — a 409 on re-ingest says what to do
 * about it — and swallowing them in favour of "Request failed" loses that.
 */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * How long to wait before giving up, per endpoint.
 *
 * fetch has no default timeout: a server that accepts the connection and then
 * never answers leaves the promise pending forever, and the UI sits on its
 * spinner with no way out but a page reload. Every request therefore carries a
 * deadline.
 *
 * Generous for /query — retrieval plus generation legitimately runs 15-30 s, and
 * a timeout that fires on a slow-but-working answer is worse than no timeout at
 * all. Short for /health, which is polled and either answers at once or is down.
 */
const TIMEOUT_MS = { '/health': 6000, '/query': 120000, default: 30000 };

function timeoutFor(path) {
  return TIMEOUT_MS[path] ?? TIMEOUT_MS.default;
}

async function request(path, options = {}) {
  const limit = timeoutFor(path);
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, limit);

  let response;
  try {
    response = await fetch(apiUrl(path), { ...options, signal: controller.signal });
  } catch (cause) {
    if (timedOut) {
      throw new ApiError(
        `The service did not respond within ${Math.round(limit / 1000)} seconds. `
        + 'It may still be starting up, or the question may have taken too long.',
        408,
      );
    }
    // fetch only rejects on a transport failure, so this is genuinely "no
    // server", not an error status.
    throw new ApiError('Cannot reach the service. Is it running?', 0);
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      /* non-JSON error body; keep the status line */
    }
    throw new ApiError(detail, response.status);
  }

  return response.status === 204 ? null : response.json();
}

export function health() {
  return request('/health');
}

export function listDocuments() {
  return request('/documents');
}

export function query({ question, docName = null, fiscalYear = null, topN = null,
                        client = null, docType = null, actVersion = null }) {
  return request('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      doc_name: docName,
      fiscal_year: fiscalYear,
      top_n: topN,
      client,
      doc_type: docType,
      act_version: actVersion,
    }),
  });
}

/**
 * Ask a question and receive the answer in stages.
 *
 * EventSource is not used: it is GET-only, and the question belongs in a body
 * rather than a URL. fetch gives a readable stream over POST, at the cost of
 * parsing the event framing here.
 *
 * Handlers: onMeta (sources and confidence, ~2 s), onDelta (a text fragment),
 * onAbstained, onDone. Exactly one of onAbstained/onDone fires. Throws ApiError
 * on transport failure or timeout, so the caller can fall back to api.query.
 */
export async function queryStream(
  { question, docName = null, fiscalYear = null, topN = null,
    client = null, docType = null, actVersion = null },
  { onMeta, onDelta, onAbstained, onDone } = {},
) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); },
    timeoutFor('/query'));

  let response;
  try {
    response = await fetch(apiUrl('/query/stream'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question, doc_name: docName, fiscal_year: fiscalYear, top_n: topN,
        client, doc_type: docType, act_version: actVersion,
      }),
      signal: controller.signal,
    });
  } catch {
    clearTimeout(timer);
    throw new ApiError(timedOut
      ? 'The service did not respond in time.'
      : 'Cannot reach the service. Is it running?', timedOut ? 408 : 0);
  }

  if (!response.ok || !response.body) {
    clearTimeout(timer);
    throw new ApiError(`${response.status} ${response.statusText}`, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line. Anything after the last separator
      // is a partial event and stays in the buffer — a JSON payload split across
      // two network reads is normal, not an error.
      const events = buffer.split('\n\n');
      buffer = events.pop() ?? '';

      for (const raw of events) {
        let name = 'message';
        let data = '';
        for (const line of raw.split('\n')) {
          if (line.startsWith('event: ')) name = line.slice(7).trim();
          else if (line.startsWith('data: ')) data += line.slice(6);
        }
        if (!data) continue;

        let payload;
        try {
          payload = JSON.parse(data);
        } catch {
          continue;   // a malformed frame should not kill a live answer
        }

        if (name === 'meta') onMeta?.(payload);
        else if (name === 'delta') onDelta?.(payload.text);
        else if (name === 'abstained') onAbstained?.(payload);
        else if (name === 'done') onDone?.(payload);
        else if (name === 'error') throw new ApiError(payload.message, 500);
      }
    }
  } finally {
    clearTimeout(timer);
    reader.cancel().catch(() => {});
  }
}

/**
 * Upload a PDF.
 *
 * XMLHttpRequest rather than fetch, purely for `upload.onprogress` — fetch has
 * no upload progress. A 30 MB annual report needs a real byte count while it
 * transfers; after that the server is parsing and embedding for minutes with
 * nothing to report, which is why the caller switches to an indeterminate bar
 * instead of inventing a percentage.
 */
export function ingest({ file, entity, fiscalYear, docName = '',
                         client = '', docType = '', actVersion = '', onProgress }) {
  const form = new FormData();
  form.append('file', file);
  form.append('entity', entity);
  form.append('fiscal_year', fiscalYear);
  if (docName) form.append('doc_name', docName);
  if (client) form.append('client', client);
  if (docType) form.append('doc_type', docType);
  if (actVersion) form.append('act_version', actVersion);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', apiUrl('/ingest'));

    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) {
        onProgress(event.loaded / event.total);
      }
    };

    // Transfer done, server still working. Nothing further is measurable.
    xhr.upload.onload = () => onProgress && onProgress(1);

    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* leave null */
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
      } else {
        reject(new ApiError(
          (body && body.detail) || `${xhr.status} ${xhr.statusText}`,
          xhr.status,
        ));
      }
    };

    xhr.onerror = () => reject(new ApiError('Upload failed: cannot reach the service.', 0));
    xhr.onabort = () => reject(new ApiError('Upload cancelled.', 0));

    xhr.send(form);
  });
}

/** URL of a document's original PDF — used by the viewer and the download link. */
export function documentFileUrl(docId) {
  return apiUrl(`/documents/${docId}/file`);
}

// ── Reconciliation (feature 06) ────────────────────────────────────────────────

/**
 * Generic SSE POST — the same frame parsing queryStream does, for any endpoint
 * that speaks the meta/delta/done contract. onEvent(name, payload) fires per
 * frame; an `error` frame throws ApiError so callers get one failure path.
 */
export async function streamSSE(path, body, onEvent, { timeoutMs = 180000 } = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);

  let response;
  try {
    response = await fetch(apiUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    clearTimeout(timer);
    throw new ApiError(timedOut
      ? 'The service did not respond in time.'
      : 'Cannot reach the service. Is it running?', timedOut ? 408 : 0);
  }

  if (!response.ok || !response.body) {
    clearTimeout(timer);
    let detail = `${response.status} ${response.statusText}`;
    try {
      const parsed = await response.json();
      if (parsed?.detail) detail = parsed.detail;
    } catch { /* keep the status text */ }
    throw new ApiError(detail, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() ?? '';
      for (const raw of events) {
        let name = 'message';
        let data = '';
        for (const line of raw.split('\n')) {
          if (line.startsWith('event: ')) name = line.slice(7).trim();
          else if (line.startsWith('data: ')) data += line.slice(6);
        }
        if (!data) continue;
        let payload;
        try { payload = JSON.parse(data); } catch { continue; }
        if (name === 'error') throw new ApiError(payload.message || 'Run failed.', 500);
        onEvent?.(name, payload);
      }
    }
  } finally {
    clearTimeout(timer);
    reader.cancel().catch(() => {});
  }
}

/** Run a reconciliation; onEvent receives ('stage'|'done', payload). */
export function runRecon({ clientId, period }, onEvent) {
  return streamSSE('/recon/run', { client_id: clientId, period }, onEvent);
}

export function reconClients() { return request('/recon/clients'); }
export function reconRuns() { return request('/recon/runs'); }
export function reconRunDetail(runId) {
  return request(`/recon/runs/${encodeURIComponent(runId)}`);
}
export function reconExceptions(runId) {
  return request(`/recon/runs/${encodeURIComponent(runId)}/exceptions`);
}
export function reconDecide(excId, action, note) {
  return request(`/recon/exceptions/${encodeURIComponent(excId)}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, note: note || null }),
  });
}
/** URL for the run-log download link — served as a file, not fetched here. */
export function reconLogUrl(runId) {
  return apiUrl(`/recon/runs/${encodeURIComponent(runId)}/log`);
}


// ── Notices (feature 07) ───────────────────────────────────────────────────────

export function listNotices() { return request('/notices'); }
export function analyzeNotice(docId, onEvent) {
  return streamSSE(`/notices/${encodeURIComponent(docId)}/analyze`, {}, onEvent);
}
export function decideReply(replyId, action, note) {
  return request(`/notices/replies/${encodeURIComponent(replyId)}/decision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, note: note || null }),
  });
}
