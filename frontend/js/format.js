/**
 * Number and text formatting.
 *
 * Indian digit grouping is not cosmetic here. A chartered accountant reads
 * `2,25,712` fluently and reads `225,712` as a mistake — it is the fastest way
 * for this UI to look foreign to the people it is being demoed to.
 *
 * There is a second, less obvious problem. The source documents are not
 * internally consistent: Infosys prints Western grouping in its CONSOLIDATED
 * statements (`162,990` on p276) and Indian grouping in its STANDALONE
 * statements (`1,36,592` on p196), within the same report. Every figure on each
 * page follows its section's convention, so this is editorial, not an
 * extraction artefact. TCS mixes them too (`255,324` on p88).
 *
 * That means answer text quoted from the corpus arrives in both conventions,
 * and any "it already has commas, leave it alone" shortcut renders `162,990` on
 * screen — which is exactly the thing a CA notices. So `normaliseNumbersInText`
 * regroups Western-grouped runs while leaving Indian-grouped ones untouched.
 */

/** Group the integer part of a number Indian-style: last 3, then pairs. */
function groupIndian(digits) {
  if (digits.length <= 3) return digits;
  const last3 = digits.slice(-3);
  const rest = digits.slice(0, -3);
  // Insert a comma before every pair, counted from the right.
  return rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3;
}

/**
 * Format a number with Indian digit grouping.
 *
 * Returns an em dash for null/undefined/NaN rather than "0" or "NaN": a missing
 * figure and a zero figure mean different things in a financial statement, and
 * showing one as the other is the kind of error this tool exists to avoid.
 */
export function formatINR(n, decimals = 2) {
  if (n === null || n === undefined || n === '') return '—';
  const value = typeof n === 'number' ? n : Number(n);
  if (!Number.isFinite(value)) return '—';

  const fixed = Math.abs(value).toFixed(decimals);
  const [int, frac] = fixed.split('.');
  const sign = value < 0 ? '-' : '';
  return sign + groupIndian(int) + (frac ? '.' + frac : '');
}

/** Integer counts — chunks, pages, documents. */
export function formatCount(n) {
  return formatINR(n, 0);
}

/** Latency in ms. Integers: sub-millisecond precision is noise here. */
export function formatMs(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(Number(ms))) return '—';
  return formatCount(Math.round(Number(ms)));
}

/**
 * Matches a comma-grouped number.
 *
 * The lookarounds are load-bearing. Without the lookbehind, `1,62,990` matches
 * its own tail `62,990` and gets "corrected" to `1,62,990` → `62,990` becoming
 * `1,62,990` inside a longer string, corrupting a figure that was already right.
 *
 * An Indian-grouped number never matches at all: after the first group this
 * requires exactly three digits, and Indian grouping puts two there.
 */
const GROUPED = /(?<![\d,.])(\d{1,3}(?:,\d{3})+)(\.\d+)?(?![\d,])/g;

/**
 * Rewrite Western-grouped numbers in prose to Indian grouping.
 *
 * Deliberately conservative — it only touches runs that are unambiguously
 * comma-grouped, so these all survive untouched:
 *
 *   FY2024-25          no commas
 *   [Page 276]         no commas
 *   Note 2.18          no commas
 *   March 31, 2025     comma followed by a space, then four digits
 *   1,62,990           already Indian; two-digit group never matches
 *   1,00,00,000        already Indian
 *
 * Ungrouped runs like `16299000` are left alone: without separators there is no
 * way to tell a rupee figure from an identifier or a page count.
 */
export function normaliseNumbersInText(text) {
  if (!text) return text;
  return String(text).replace(GROUPED, (match, intPart, fracPart) => {
    const digits = intPart.replace(/,/g, '');
    return groupIndian(digits) + (fracPart || '');
  });
}

/** Escape for interpolation into HTML. */
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Human label for a basis value.
 *
 * null means the basis could not be determined from the report's structure. It
 * is rendered as a stated unknown, never blank and never defaulted to
 * consolidated — silently resolving it is the failure this field exists to
 * prevent.
 */
export function basisLabel(basis) {
  if (basis === 'consolidated') return 'Consolidated';
  if (basis === 'standalone') return 'Standalone';
  return 'Basis not determined';
}

/** Short form for the provenance chip, where horizontal space is tight. */
export function basisShort(basis) {
  if (basis === 'consolidated') return 'Consolidated';
  if (basis === 'standalone') return 'Standalone';
  return 'Basis unknown';
}

/**
 * Zero-padded page label, so citations in a list stay visually aligned.
 * A missing page is not padded — `p.00?` reads as a number, `p.?` as an absence.
 */
export function pageLabel(page) {
  if (page === null || page === undefined || page === '') return 'p.?';
  return 'p.' + String(page).padStart(3, '0');
}
