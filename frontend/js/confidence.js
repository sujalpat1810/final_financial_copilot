/**
 * Presentation for the confidence states.
 *
 * The backend decides the level (app/confidence.py) from raw reranker logits.
 * This module only decides how it looks — no thresholds live here, so the
 * frontend can never disagree with the label the server computed.
 *
 * Colour is never the only signal. Every state pairs a colour with an icon and
 * a word, so it survives being projected in a meeting room and reads for a
 * colourblind partner.
 */

const STATES = {
  high: {
    label: 'High confidence',
    icon: 'i-check',
    className: 'high',
    // Read out to screen readers in place of the visual badge.
    description: 'Strong evidence, corroborated by more than one passage.',
  },
  moderate: {
    label: 'Moderate confidence',
    icon: 'i-warn',
    className: 'moderate',
    description: 'Usable evidence, but thinner — check the sources.',
  },
  low: {
    label: 'Low confidence',
    icon: 'i-warn',
    className: 'low',
    description: 'Weak evidence. Treat this answer with caution.',
  },
  insufficient: {
    label: 'Insufficient evidence',
    icon: 'i-shield',
    className: 'insufficient',
    description: 'Below the abstention threshold. No answer was generated.',
  },
};

/**
 * Unknown levels fall back to the most cautious state rather than the friendliest.
 * If the server ever sends something this build does not recognise, showing
 * "Low confidence" is safe; defaulting to "High" would not be.
 */
export function confidenceState(level) {
  return STATES[level] || { ...STATES.low, label: 'Confidence unknown' };
}

export function isAbstention(response) {
  return Boolean(response && response.abstained);
}

/**
 * Relevance is a 0-100 display transform of the reranker logit, computed
 * server-side. Monotone, but not a probability — do not label it one.
 *
 * Below the moderate band it is drawn in grey rather than accent, so a
 * near-miss on the abstention card does not read as a good match.
 */
export function relevanceIsWeak(relevance) {
  return relevance < 40;
}
