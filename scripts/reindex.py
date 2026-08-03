"""
Populate the configured vector store from the chunk store.

Why this exists separately from scripts.ingest
──────────────────────────────────────────────
`scripts.ingest` parses PDFs, and it is deliberately idempotent against the
content hash recorded in data/chunk_store.json: a document already indexed with
identical content raises AlreadyIndexed and is skipped, so a re-run after an
interruption costs a hash instead of a re-parse.

That is exactly the wrong behaviour after switching VECTOR_STORE_BACKEND.  The
documents are still recorded in the chunk store, so ingest skips all of them and
the new backend stays empty — a switch that appears to succeed and leaves the
service with nothing to retrieve.

This script starts from the chunk store instead of from the PDFs.  The chunk
store holds chunk text and full provenance and is backend-independent, so it is
the right source of truth: no PDF is re-parsed, no chunk boundary moves, and the
new backend ends up with byte-identical text to the old one.  Only the embeddings
are recomputed, which is the one thing a vector store cannot be given secondhand.

Usage
─────
    python -m scripts.reindex              # fill the configured backend
    python -m scripts.reindex --dry-run    # report the plan, load no models
    python -m scripts.reindex --force      # add even if the store is non-empty

Switching backends is two steps:

    1. set VECTOR_STORE_BACKEND (and any Chroma Cloud credentials) in .env
    2. python -m scripts.reindex
"""

from __future__ import annotations

import argparse
import sys
import time

# Must precede any app import: app.config reads os.environ at import time, so a
# backend or credential set only in .env would otherwise be invisible here and
# this script would cheerfully populate the wrong store.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Chunks are embedded in batches so a 2000-chunk corpus reports progress instead
# of sitting silent for a minute.
BATCH = 128


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Populate the configured vector store from data/chunk_store.json.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen; load no models")
    parser.add_argument("--force", action="store_true",
                        help="proceed even if the target store already holds chunks")
    parser.add_argument("--batch", type=int, default=BATCH,
                        help=f"embedding batch size (default {BATCH})")
    args = parser.parse_args(argv)

    from app.config import cfg
    from app.ingestion import get_all_chunks

    backend = cfg.vector_store_backend
    target = backend
    if backend == "chroma":
        target += " (cloud)" if cfg.chroma_is_cloud else " (local)"

    print(f"backend  : {target}")
    if cfg.chroma_is_cloud:
        print(f"tenant   : {cfg.chroma_tenant}")
        print(f"database : {cfg.chroma_database}")

    chunks = get_all_chunks()
    if not chunks:
        print("\nNothing to do: data/chunk_store.json holds no chunks.")
        print("Ingest the corpus first:  python -m scripts.ingest")
        return 1

    docs = {c.metadata.doc_name for c in chunks}
    print(f"chunks   : {len(chunks)} across {len(docs)} documents")
    for name in sorted(docs):
        n = sum(1 for c in chunks if c.metadata.doc_name == name)
        print(f"           {n:>5}  {name}")

    if args.dry_run:
        print(f"\nwould embed {len(chunks)} chunks and write them to {target}")
        return 0

    print("\nloading embedding model...", flush=True)
    from app.retrieval import EmbeddingModel
    from app.vector_store import get_vector_store

    embed = EmbeddingModel()
    store = get_vector_store()

    existing = store.get_chunk_count()
    if existing and not args.force:
        # Neither backend deduplicates on re-add: FAISS appends blindly, and
        # Chroma rejects ids it already holds.  Either way a second run without
        # this guard corrupts the index rather than refreshing it.
        print(f"\nRefusing to write: {target} already holds {existing} chunks.")
        print("Clear the collection first, or pass --force to add anyway.")
        return 1

    started = time.perf_counter()
    written = 0
    for i in range(0, len(chunks), args.batch):
        batch = chunks[i:i + args.batch]
        vectors = embed.embed_documents([c.text for c in batch])
        store.add_chunks(batch, vectors)
        written += len(batch)
        pct = written / len(chunks) * 100
        elapsed = time.perf_counter() - started
        print(f"  {written:>5}/{len(chunks)}  ({pct:5.1f}%)  {elapsed:6.1f}s",
              flush=True)

    elapsed = time.perf_counter() - started
    final = store.get_chunk_count()
    print(f"\nwrote {written} chunks to {target} in {elapsed:.1f}s")
    print(f"store now reports {final} chunks")

    if final != existing + written:
        # Worth saying out loud: a silent shortfall here means retrieval will
        # quietly miss pages, which looks like a bad answer rather than a bad
        # index.
        print(f"WARNING: expected {existing + written}, got {final}.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
