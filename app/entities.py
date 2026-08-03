"""
Refusing questions about companies that were never indexed.

The problem this solves
───────────────────────
Reranker scores do not separate "no answer here" from "an answer about a
different company".  Measured on the demo corpus (data/calibration.json), the
six questions about unindexed companies scored:

    melting point of tungsten          -11.20
    lunar mining operations             -8.99
    HDFC Bank net interest margin       -5.60
    Reliance Industries headcount       -4.70
    State Bank of India CAR             -2.44
    Wipro revenue FY2025                +1.33

Only the first two fall below the -6.0 abstention floor.  The other four are
about real peer companies asking real financial questions, so the passages that
come back — an Infosys revenue table, a TCS headcount note — are genuinely good
matches for the *shape* of the question.  The cross-encoder is not wrong; it was
never asked whether the company matched.

Raising the floor cannot fix this.  Legitimate open questions on the same corpus
run down to -2.24 ("What was the profit for the year?"), so any threshold that
catches Wipro at +1.33 also throws away half the questions the tool exists to
answer.  The bands overlap because score measures topical similarity, and
topical similarity is exactly what a peer company's financials have.

So the gate is categorical, not scalar: if the question names a company and that
company is not in the index, there is no score high enough to make answering it
correct.

Precision over recall
─────────────────────
This gate only fires on names it positively recognises — a curated peer
gazetteer plus a corporate-suffix rule ("... Ltd", "... Bank").  An unrecognised
company falls through to the existing score-based floor, exactly as today.

That asymmetry is deliberate.  A false positive here refuses a question the
corpus could have answered, which looks broken; a false negative leaves
behaviour unchanged from what already ships.  A gate that guesses at every
capitalised token would trip over "Ind AS", "March", "India" and "Board of
Directors" — all frequent in these questions, none of them companies.
"""

from __future__ import annotations

import re

# ── Aliases for entities that may be indexed ──────────────────────────────────
# Maps an alias to the canonical `entity` string used at ingest time.  The
# indexed set is read from the store at call time, so ingesting Wipro stops this
# gate firing on Wipro without any code change — but the alias table still has
# to know that "Tata Consultancy Services" and "TCS" are the same company, since
# only one of them is what the operator typed into --entity.
_ALIASES: dict[str, str] = {
    "infosys": "Infosys",
    "infosys limited": "Infosys",
    "infosys ltd": "Infosys",
    "tcs": "TCS",
    "tata consultancy services": "TCS",
    "tata consultancy services limited": "TCS",
}

# ── Peer companies this corpus does NOT cover ─────────────────────────────────
# Chosen for what a reader actually asks an Indian-IT annual-report tool: direct
# competitors, the large-cap names that share a screen with them, and the global
# services firms they benchmark against.  Longest phrase wins at match time, so
# "State Bank of India" is not shadowed by a shorter entry.
#
# Maps the lowercase match key to how the name should be PRINTED.  Deriving the
# display form instead — .title() — produces "Hdfc Bank" and "State Bank Of
# India", which in a refusal shown to accountants reads as though the tool does
# not know the company it is declining to discuss.
_KNOWN_COMPANIES: dict[str, str] = {
    # Indian IT services
    "wipro": "Wipro", "hcl": "HCL", "hcl technologies": "HCL Technologies",
    "hcltech": "HCLTech", "tech mahindra": "Tech Mahindra",
    "ltimindtree": "LTIMindtree", "l&t infotech": "L&T Infotech",
    "mindtree": "Mindtree", "mphasis": "Mphasis", "coforge": "Coforge",
    "persistent systems": "Persistent Systems", "zensar": "Zensar",
    "birlasoft": "Birlasoft", "cyient": "Cyient", "kpit": "KPIT",
    # Global IT services / consulting
    "accenture": "Accenture", "cognizant": "Cognizant", "capgemini": "Capgemini",
    "ibm": "IBM", "dxc": "DXC", "dxc technology": "DXC Technology",
    "genpact": "Genpact", "epam": "EPAM", "globant": "Globant", "atos": "Atos",
    "ntt data": "NTT Data", "deloitte": "Deloitte", "pwc": "PwC", "kpmg": "KPMG",
    "ernst & young": "Ernst & Young", "mckinsey": "McKinsey",
    # Indian large caps commonly asked about
    "reliance": "Reliance", "reliance industries": "Reliance Industries",
    "ril": "RIL", "tata motors": "Tata Motors", "tata steel": "Tata Steel",
    "tata power": "Tata Power", "titan": "Titan", "adani": "Adani",
    "adani enterprises": "Adani Enterprises", "adani ports": "Adani Ports",
    "bharti airtel": "Bharti Airtel", "airtel": "Airtel", "jio": "Jio",
    "vodafone idea": "Vodafone Idea", "itc": "ITC",
    "hindustan unilever": "Hindustan Unilever", "hul": "HUL",
    "nestle india": "Nestlé India", "maruti": "Maruti",
    "maruti suzuki": "Maruti Suzuki", "mahindra & mahindra": "Mahindra & Mahindra",
    "larsen & toubro": "Larsen & Toubro", "l&t": "L&T",
    "asian paints": "Asian Paints", "ultratech": "UltraTech",
    "sun pharma": "Sun Pharma", "dr reddy's": "Dr Reddy's", "cipla": "Cipla",
    "divi's laboratories": "Divi's Laboratories",
    "bajaj finance": "Bajaj Finance", "bajaj finserv": "Bajaj Finserv",
    "jsw steel": "JSW Steel", "coal india": "Coal India", "ongc": "ONGC",
    # Banks and financials
    "hdfc": "HDFC", "hdfc bank": "HDFC Bank", "icici": "ICICI",
    "icici bank": "ICICI Bank", "axis bank": "Axis Bank",
    "state bank of india": "State Bank of India", "sbi": "SBI",
    "kotak mahindra bank": "Kotak Mahindra Bank", "kotak": "Kotak",
    "indusind bank": "IndusInd Bank", "yes bank": "Yes Bank",
    "punjab national bank": "Punjab National Bank", "pnb": "PNB",
    "bank of baroda": "Bank of Baroda", "canara bank": "Canara Bank",
    "idfc first bank": "IDFC First Bank",
    "au small finance bank": "AU Small Finance Bank",
    "paytm": "Paytm", "one97": "One97", "zerodha": "Zerodha",
    "bajaj housing finance": "Bajaj Housing Finance",
    # Global tech
    "microsoft": "Microsoft", "google": "Google", "alphabet": "Alphabet",
    "amazon": "Amazon", "apple": "Apple", "meta": "Meta", "facebook": "Facebook",
    "oracle": "Oracle", "sap": "SAP", "salesforce": "Salesforce",
    "nvidia": "NVIDIA", "intel": "Intel", "tesla": "Tesla", "netflix": "Netflix",
    "samsung": "Samsung", "adobe": "Adobe", "cisco": "Cisco", "dell": "Dell",
    "hp": "HP", "hewlett packard": "Hewlett Packard",
}

# Tokens that look like a company suffix.  A capitalised run ending in one of
# these is treated as a company name even when the gazetteer has never heard of
# it, which is what catches the long tail of smaller names.
_SUFFIX_ALT = r"limited|ltd\.?|inc\.?|corp\.?|corporation|plc|llp|gmbh|ag|nv|sa|bank"
_SUFFIXES = r"(?:" + _SUFFIX_ALT + r")"

# A capitalised run followed by a corporate suffix, e.g. "Zomato Limited".
# Requires the suffix: a bare capitalised run matches far too much.
#
# The leading run is deliberately case-SENSITIVE while only the suffix is
# case-insensitive, via a scoped (?i:) group.  A blanket re.IGNORECASE here
# makes [A-Z] match anything and the pattern swallows the whole question stem —
# "What was Zomato Limited" came back as the company name.
_SUFFIX_PATTERN = re.compile(
    r"\b((?:[A-Z][\w&'’\-]*\s+){0,3}[A-Z][\w&'’\-]*\s+(?i:" + _SUFFIX_ALT + r"))\b"
)


def _normalise(name: str) -> str:
    """Lowercase and collapse whitespace, so matching is punctuation-tolerant."""
    return re.sub(r"\s+", " ", name.strip().lower())


def _strip_suffix(name: str) -> str:
    """Drop a trailing corporate suffix so 'Wipro Limited' resolves to 'wipro'."""
    return re.sub(r"\s+" + _SUFFIXES + r"$", "", _normalise(name)).strip()


def canonical_entity(name: str) -> str | None:
    """Resolve an alias to the canonical entity string, or None if unrecognised."""
    n = _normalise(name)
    return _ALIASES.get(n) or _ALIASES.get(_strip_suffix(n))


def find_mentions(question: str) -> list[str]:
    """
    Every company name the question mentions, as written.

    Longest-first matching with span consumption, so "Tata Consultancy Services"
    is returned once as itself rather than also matching a shorter overlapping
    entry.  Without that, an alias table entry for "Tata …" would make every TCS
    question look like it named a second, unindexed company.
    """
    text = _normalise(question)
    found: list[str] = []
    consumed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in consumed)

    candidates = sorted(
        set(_KNOWN_COMPANIES) | set(_ALIASES), key=len, reverse=True
    )
    for cand in candidates:
        # Word boundaries, so "hp" does not match inside "hpcl" and "sbi" does
        # not match inside "sbin".  re.escape keeps "l&t" and "dr reddy's" literal.
        for m in re.finditer(rf"(?<!\w){re.escape(cand)}(?!\w)", text):
            if not overlaps(m.start(), m.end()):
                consumed.append((m.start(), m.end()))
                found.append(cand)

    # Suffix rule runs on the ORIGINAL casing — it keys off capitalisation.
    for m in _SUFFIX_PATTERN.finditer(question):
        raw = _normalise(m.group(1))
        start = text.find(raw)
        if start >= 0 and not overlaps(start, start + len(raw)):
            consumed.append((start, start + len(raw)))
            found.append(raw)

    return found


def foreign_entities(question: str, indexed: set[str]) -> list[str]:
    """
    Companies the question names that are NOT in the indexed set.

    `indexed` holds canonical entity strings as recorded at ingest
    (e.g. {"Infosys", "TCS"}).  Comparison is case-insensitive: the operator
    types --entity by hand and "infosys" must not read as a different company
    from "Infosys".
    """
    indexed_norm = {_normalise(e) for e in indexed}
    out: list[str] = []
    for mention in find_mentions(question):
        canon = canonical_entity(mention)
        if canon and _normalise(canon) in indexed_norm:
            continue
        if _normalise(mention) in indexed_norm or _strip_suffix(mention) in indexed_norm:
            continue
        if mention not in out:
            out.append(mention)
    return out


def display_name(name: str) -> str:
    """
    How a matched name should be printed.

    Prefers the curated spelling, then the canonical entity string, and only
    then falls back to title-casing — which is correct for the suffix rule's
    long tail ("zomato limited" → "Zomato Limited") but mangles acronyms, so it
    is the last resort rather than the first.
    """
    key = _normalise(name)
    return _KNOWN_COMPANIES.get(key) or canonical_entity(key) or key.title()


def describe(names: list[str]) -> str:
    """Join names into readable prose for an abstention message."""
    pretty = [display_name(n) for n in names]
    if len(pretty) == 1:
        return pretty[0]
    return ", ".join(pretty[:-1]) + " and " + pretty[-1]
