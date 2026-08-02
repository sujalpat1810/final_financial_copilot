"""
Bulk-ingest the demo corpus from the command line.

Why not the HTTP endpoint
─────────────────────────
A 300-page integrated annual report is 20-40 MB and takes minutes to parse and
embed.  Uploading six of those through /ingest before a demo means six long
requests with nothing but a progress bar to look at, and any interruption loses
the work.  This talks to the ingestion pipeline directly, prints real progress,
and skips documents that are already indexed with identical content — so a
re-run after an interruption picks up where it stopped instead of reprocessing
everything.

entity and fiscal_year are required per document and are NOT detected.  An annual
report is full of comparative columns, so any heuristic that reads a year off the
page is guessing, and attributing a figure to the wrong company or year is the
failure this tool exists to prevent.  Supply them in the manifest.

Usage
─────
    # ingest everything named in the manifest
    python -m scripts.ingest

    # a different manifest, or specific documents from it
    python -m scripts.ingest --manifest my_corpus.json
    python -m scripts.ingest --only infosys-ar-25.pdf

    # see what would happen, touching nothing
    python -m scripts.ingest --dry-run

The manifest (scripts/corpus.json by default) is a list of objects:

    [
      {"file": "pdf_data/infosys-ar-25.pdf",
       "doc_name": "Infosys FY2024-25",
       "entity": "Infosys",
       "fiscal_year": "FY2024-25"}
    ]

doc_name is optional and defaults to the filename stem.  It determines doc_id,
so changing it later creates a second document rather than replacing the first.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_MANIFEST = Path("scripts/corpus.json")


def _load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"Manifest not found: {path}\n"
            f"Create it as a JSON list of "
            f'{{"file", "doc_name", "entity", "fiscal_year"}} objects - see the '
            f"docstring in {__file__}."
        )
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON: {e}")

    if not isinstance(entries, list):
        raise SystemExit(f"{path} must contain a JSON list.")

    for i, entry in enumerate(entries):
        missing = [k for k in ("file", "entity", "fiscal_year") if not entry.get(k)]
        if missing:
            raise SystemExit(
                f"{path} entry {i} is missing required field(s): {', '.join(missing)}. "
                f"entity and fiscal_year are never inferred from the document."
            )
    return entries


def _fmt(n: int) -> str:
    """Indian digit grouping, to match everything else the operator sees."""
    s = str(abs(n))
    if len(s) <= 3:
        grouped = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + grouped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bulk-ingest annual reports listed in a manifest.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--only", action="append", default=[],
                        help="ingest only entries whose file path contains this string; repeatable")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be ingested without loading models")
    args = parser.parse_args(argv)

    entries = _load_manifest(args.manifest)
    if args.only:
        entries = [e for e in entries if any(k in e["file"] for k in args.only)]
        if not entries:
            raise SystemExit(f"--only {args.only} matched nothing in {args.manifest}.")

    # Imported here, not at module scope, so --dry-run and --help stay instant:
    # app.ingestion is cheap but app.retrieval pulls in torch.
    from app.ingestion import (
        AlreadyIndexed,
        ContentConflict,
        file_sha256,
        ingest_pdf,
        list_documents,
    )

    print(f"manifest : {args.manifest}")
    print(f"documents: {len(entries)}\n")

    missing = [e["file"] for e in entries if not Path(e["file"]).exists()]
    if missing:
        print("MISSING FILES - nothing was ingested:")
        for m in missing:
            print(f"  {m}")
        return 1

    if args.dry_run:
        for e in entries:
            size_mb = Path(e["file"]).stat().st_size / 1e6
            print(f"  would ingest {e['file']}  ({size_mb:.1f} MB)")
            print(f"      doc_name    {e.get('doc_name') or Path(e['file']).stem}")
            print(f"      entity      {e['entity']}")
            print(f"      fiscal year {e['fiscal_year']}")
            print(f"      sha256      {file_sha256(e['file'])[:16]}...")
        return 0

    # Models load once and are reused across every document.
    print("loading embedding model...", flush=True)
    from app.retrieval import BM25Index, EmbeddingModel
    from app.vector_store import get_vector_store

    embed = EmbeddingModel()
    store = get_vector_store()
    bm25 = BM25Index()

    ingested = skipped = failed = 0
    started = time.perf_counter()

    for entry in entries:
        path = entry["file"]
        doc_name = entry.get("doc_name") or Path(path).stem
        # ASCII only: Windows consoles are cp1252 and mojibake anything else,
        # which would make the hashes and counts below unreadable.
        print("\n-- " + doc_name + " " + "-" * max(4, 44 - len(doc_name)))
        print(f"   file   {path}")
        t0 = time.perf_counter()

        try:
            doc_id, chunks = ingest_pdf(
                path,
                doc_name,
                entity=entry["entity"],
                fiscal_year=entry["fiscal_year"],
            )
        except AlreadyIndexed:
            # The whole point of the content hash: a re-run after an interruption
            # costs a hash, not a re-parse.
            print("   SKIP   already indexed with identical content")
            skipped += 1
            continue
        except ContentConflict as e:
            print(f"   FAIL   {e}")
            failed += 1
            continue
        except Exception as e:
            print(f"   FAIL   {type(e).__name__}: {e}")
            failed += 1
            continue

        if not chunks:
            print("   FAIL   no text extracted")
            failed += 1
            continue

        parse_s = time.perf_counter() - t0
        pages = len({c.metadata.page_number for c in chunks})
        standalone = sum(1 for c in chunks if c.metadata.basis == "standalone")
        consolidated = sum(1 for c in chunks if c.metadata.basis == "consolidated")
        undetermined = len(chunks) - standalone - consolidated

        print(f"   parsed {_fmt(pages)} pages -> {_fmt(len(chunks))} chunks  ({parse_s:.0f}s)")
        # Basis counts are printed per document so a detection failure is visible
        # here rather than surfacing later as unqualified answers in the UI.
        print(f"   basis  standalone {_fmt(standalone)} | consolidated "
              f"{_fmt(consolidated)} | undetermined {_fmt(undetermined)}")
        if standalone == 0 and consolidated == 0:
            print("   WARN   no basis detected anywhere - every answer from this "
                  "document will be qualified as undetermined. Check "
                  "app/basis.py against this publisher's headings.")

        t1 = time.perf_counter()
        print(f"   embedding {_fmt(len(chunks))} chunks...", flush=True)
        embeddings = embed.embed_documents([c.indexed_text for c in chunks])
        store.add_chunks(chunks, embeddings)
        bm25.add_chunks(chunks)
        print(f"   indexed  ({time.perf_counter() - t1:.0f}s)")
        ingested += 1

    elapsed = time.perf_counter() - started
    print(f"\n{'='*52}")
    print(f"ingested {ingested} | skipped {skipped} | failed {failed}"
          f"   in {elapsed:.0f}s")
    for doc in list_documents():
        flag = "" if doc.has_file else "   [no stored PDF]"
        print(f"  {doc.doc_name:<28} {doc.entity or '?':<10} {doc.fiscal_year or '?':<11} "
              f"{_fmt(doc.chunks):>7} chunks{flag}")

    if ingested:
        # BM25 is rebuilt from the persisted chunk store at startup, so it does not
        # need saving here — but that is worth verifying rather than assuming.
        print("\nRestart the server to pick up the new index, then confirm "
              "/health reports the expected chunk count.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
