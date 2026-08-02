"""
Measure reranker scores against the real corpus, and derive the thresholds.

Why this exists
───────────────
The four numbers in app/config.py — the high/moderate/abstain thresholds and
the minimum supporting count — are not derivable from the code. They depend on
how cross-encoder/ms-marco-MiniLM-L-6-v2 happens to score these particular
documents, and that model was trained on English web passages, not pipe-
delimited Ind AS tables. The shipped values are placeholders.

Getting them wrong breaks the two most important demo moments in opposite
directions:

  floor too high  every question abstains, including answerable ones
  floor too low   a question about an unindexed company invents an answer

So they are measured, not guessed.

What it reports
───────────────
  1. Retrieval quality — for questions with a known correct page, does that page
     appear in the top-N at all, and at what rank? A perfect threshold cannot
     rescue retrieval that never surfaced the right chunk.
  2. Score distribution — top score per question, split into answerable and
     unanswerable groups.
  3. A recommended abstain floor, placed in the gap between the two groups, and
     whether a clean gap exists at all.

Run after ingesting the corpus:
    python -m scripts.calibrate
    python -m scripts.calibrate --top-n 10 --json out.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# ── Probe set ─────────────────────────────────────────────────────────────────
# expect: (doc_name substring, page) that a correct answer must be able to cite.
# None means the corpus cannot answer it and the system must abstain.

PROBES = [
    # Answerable, with a known page. Pages measured from the PDFs.
    {"q": "What was Infosys consolidated revenue in FY2024-25?",
     "expect": ("Infosys FY2024-25", 276)},
    {"q": "What was Infosys standalone revenue in FY2024-25?",
     "expect": ("Infosys FY2024-25", 196)},
    {"q": "What was TCS consolidated revenue in FY2024-25?",
     "expect": ("TCS FY2024-25", 181)},
    {"q": "What was TCS standalone revenue in FY2024-25?",
     "expect": ("TCS FY2024-25", 253)},
    {"q": "What was Infosys consolidated revenue in FY2025-26?",
     "expect": ("Infosys FY2025-26", 287)},

    # Answerable, page not pinned — prose and table retrieval of other kinds.
    {"q": "Who audited Infosys and was the opinion unqualified?", "expect": "any"},
    {"q": "What contingent liabilities are disclosed?", "expect": "any"},
    {"q": "List related party transactions", "expect": "any"},
    {"q": "What was the profit for the year?", "expect": "any"},
    {"q": "What are the key risk factors?", "expect": "any"},
    {"q": "What is the dividend per share?", "expect": "any"},
    {"q": "What was revenue?", "expect": "any"},   # unqualified, still answerable

    # Must abstain. These set the floor.
    {"q": "What was Wipro's revenue in FY2025?", "expect": None},
    {"q": "What was HDFC Bank's net interest margin?", "expect": None},
    {"q": "How many employees does Reliance Industries have?", "expect": None},
    {"q": "What is the capital adequacy ratio of State Bank of India?", "expect": None},
    {"q": "What did the company say about lunar mining operations?", "expect": None},
    {"q": "What is the melting point of tungsten?", "expect": None},
]


def _fmt(value: float | None) -> str:
    return "     -" if value is None else f"{value:6.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure reranker scores and derive confidence thresholds.")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--json", type=Path, help="write the raw measurements here")
    args = parser.parse_args(argv)

    from app.config import cfg
    from app.ingestion import get_all_chunks, list_documents
    from app.retrieval import BM25Index, EmbeddingModel, HybridRetriever, Reranker
    from app.vector_store import get_vector_store

    documents = list_documents()
    if not documents:
        print("No documents indexed. Run `python -m scripts.ingest` first.")
        return 1

    print(f"corpus   : {len(documents)} documents")
    for doc in documents:
        print(f"           {doc.doc_name:<24} {doc.chunks:>6} chunks")
    print(f"reranker : {cfg.reranker_model}\n")

    print("loading models...", flush=True)
    chunks = get_all_chunks()
    bm25 = BM25Index()
    bm25.build(chunks)
    retriever = HybridRetriever(
        vector_store=get_vector_store(),
        bm25_index=bm25,
        embedding_model=EmbeddingModel(),
        reranker=Reranker(),
    )

    measurements = []
    print(f"\n{'=' * 78}")

    for probe in PROBES:
        results = retriever.retrieve(query=probe["q"], top_n=args.top_n)
        scores = [r.rerank_score for r in results if r.rerank_score is not None]
        top = max(scores) if scores else None

        # Where did the expected page land?
        rank = None
        if isinstance(probe["expect"], tuple):
            want_doc, want_page = probe["expect"]
            for i, r in enumerate(results, start=1):
                m = r.chunk.metadata
                if want_doc in m.doc_name and m.page_number == want_page:
                    rank = i
                    break

        kind = ("absent" if probe["expect"] is None
                else "pinned" if isinstance(probe["expect"], tuple) else "open")
        measurements.append({
            "question": probe["q"], "kind": kind, "top": top,
            "scores": scores, "expected_rank": rank,
            "top_hits": [
                {"doc": r.chunk.metadata.doc_name, "page": r.chunk.metadata.page_number,
                 "basis": r.chunk.metadata.basis, "score": r.rerank_score,
                 "source": r.retrieval_source}
                for r in results[:3]
            ],
        })

        verdict = ""
        if kind == "pinned":
            verdict = f"expected page at rank {rank}" if rank else "EXPECTED PAGE NOT RETRIEVED"
        print(f"\n[{kind:6}] {probe['q']}")
        print(f"          top={_fmt(top)}   {verdict}")
        for hit in measurements[-1]["top_hits"]:
            basis = hit["basis"] or "undetermined"
            print(f"            {_fmt(hit['score'])}  {hit['doc']:<20} p{hit['page']:<4} "
                  f"{basis:<13} {hit['source']}")

    # ── Summary ───────────────────────────────────────────────────────────────
    answerable = [m["top"] for m in measurements if m["kind"] != "absent" and m["top"] is not None]
    absent = [m["top"] for m in measurements if m["kind"] == "absent" and m["top"] is not None]

    print(f"\n{'=' * 78}\nRETRIEVAL QUALITY")
    pinned = [m for m in measurements if m["kind"] == "pinned"]
    hits = [m for m in pinned if m["expected_rank"]]
    print(f"  expected page in top-{args.top_n}: {len(hits)}/{len(pinned)}")
    for m in pinned:
        mark = f"rank {m['expected_rank']}" if m["expected_rank"] else "MISS"
        print(f"    {mark:<8} {m['question']}")
    if len(hits) < len(pinned):
        print("\n  A miss cannot be fixed by tuning thresholds — the right chunk was\n"
              "  never retrieved. Look at chunking or the candidate pool sizes\n"
              "  (VECTOR_TOP_K / BM25_TOP_K) before touching confidence.")

    print(f"\nSCORE DISTRIBUTION")
    for label, group in (("answerable", answerable), ("unanswerable", absent)):
        if group:
            print(f"  {label:<13} n={len(group):<3} min={min(group):6.2f}  "
                  f"median={statistics.median(group):6.2f}  max={max(group):6.2f}")

    print(f"\nTHRESHOLD RECOMMENDATION")
    if answerable and absent:
        worst_answerable, best_absent = min(answerable), max(absent)
        if worst_answerable > best_absent:
            floor = round((worst_answerable + best_absent) / 2, 1)
            print(f"  Clean separation: every answerable question scored above every")
            print(f"  unanswerable one ({worst_answerable:.2f} vs {best_absent:.2f}).")
            print(f"    RERANK_ABSTAIN_THRESHOLD={floor}")
        else:
            # Overlap means no floor can satisfy both. Say so rather than
            # producing a number that looks authoritative.
            floor = round(best_absent + 0.1, 1)
            print(f"  OVERLAP: the worst answerable question ({worst_answerable:.2f}) scores")
            print(f"  below the best unanswerable one ({best_absent:.2f}). No single floor")
            print(f"  separates them — any value trades false abstentions against")
            print(f"  invented answers. Prefer false abstentions for this audience:")
            print(f"    RERANK_ABSTAIN_THRESHOLD={floor}   (abstains on some answerable questions)")
        if answerable:
            high = round(statistics.median(answerable), 1)
            print(f"    RERANK_HIGH_THRESHOLD={high}       (median answerable top score)")
            print(f"    RERANK_MODERATE_THRESHOLD={round((high + floor) / 2, 1)}")

    if args.json:
        args.json.write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        print(f"\nraw measurements -> {args.json}")

    print("\nSet these in .env, then re-run to confirm the labels come out as expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
