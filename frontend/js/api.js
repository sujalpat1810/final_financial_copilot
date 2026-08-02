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
 */
const API_ROOT = new URL('../', new URL('./', window.location.href));

function apiUrl(path) {
  return new URL(path.replace(/^\//, ''), API_ROOT).toString();
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

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(apiUrl(path), options);
  } catch (cause) {
    // fetch only rejects on a transport failure, so this is genuinely "no
    // server", not an error status.
    throw new ApiError('Cannot reach the service. Is it running?', 0);
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

export function query({ question, docName = null, fiscalYear = null, topN = null }) {
  return request('/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      question,
      doc_name: docName,
      fiscal_year: fiscalYear,
      top_n: topN,
    }),
  });
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
export function ingest({ file, entity, fiscalYear, docName = '', onProgress }) {
  const form = new FormData();
  form.append('file', file);
  form.append('entity', entity);
  form.append('fiscal_year', fiscalYear);
  if (docName) form.append('doc_name', docName);

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
