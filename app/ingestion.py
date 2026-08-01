"""
PDF ingestion pipeline: parse → extract text+tables → chunk → dual-index.

Why pdfplumber over pypdf alone?
  pdfplumber exposes table detection via its page.extract_tables() API, which
  returns structured rows/columns.  pypdf is used as a fast fallback for
  text-only pages.  We try pdfplumber first; if it returns empty text we fall
  back to pypdf.

Chunking strategy — semantic boundaries, not fixed windows:
  1. Split the raw page text into paragraphs (double-newline boundaries).
  2. Merge short paragraphs with their neighbours so no chunk is tiny.
  3. Each chunk is limited to ~MAX_CHUNK_TOKENS words (not subword tokens —
     close enough for embedding models and avoids a heavy tokenizer dep).
  4. A 1-sentence overlap is kept between adjacent chunks to preserve context
     across boundaries.

LlamaIndex role here:
  We use LlamaIndex's SimpleDocumentStore as the page-level document store —
  it provides a clean node/document abstraction with built-in metadata support
  and serialisation, which keeps page-level documents separate from the
  embedding-level chunk objects used by LangChain.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.basis import assign_basis
from app.config import cfg
from app.models import Chunk, ChunkMetadata, DocumentInfo


# Fiscal year is NOT detected.  A regex that took the first four-digit year on a
# page returned garbage on real annual reports — any comparative column supplies
# a competing year — and propagating the most common year across 300 pages was a
# lottery.  entity and fiscal_year are now supplied by the operator at ingest.
# Basis IS detected, from section structure rather than keywords: see app/basis.py.


# ── Section heading detector ──────────────────────────────────────────────────

_HEADING_PATTERNS = [
    re.compile(r"^(ITEM\s+\d+[A-Z]?\.?\s+.+)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^([A-Z][A-Z\s]{5,50})$", re.MULTILINE),          # ALL-CAPS lines
    re.compile(r"^(\d+\.\s+[A-Z][^.]{5,60})$", re.MULTILINE),     # numbered headings
]


def _detect_section_heading(text: str) -> str | None:
    for pat in _HEADING_PATTERNS:
        m = pat.search(text[:500])   # only look in the first ~500 chars of a page
        if m:
            return m.group(1).strip()[:120]
    return None


# ── Table formatter ───────────────────────────────────────────────────────────

def _table_to_text(table: list[list[str | None]]) -> str:
    """Convert a pdfplumber table to a pipe-delimited markdown-ish string."""
    rows = []
    for row in table:
        cells = [str(c).strip() if c else "" for c in row]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


# ── PDF text extraction ───────────────────────────────────────────────────────

def _extract_pages(pdf_path: str) -> list[dict[str, Any]]:
    """
    Returns a list of page dicts:
      { page_number, text, section_heading }
    """
    pages: list[dict] = []

    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                parts: list[str] = []

                raw_text = page.extract_text() or ""
                if raw_text.strip():
                    parts.append(raw_text)

                tables = page.extract_tables() or []
                for table in tables:
                    parts.append("\n[TABLE]\n" + _table_to_text(table) + "\n[/TABLE]\n")

                text = "\n".join(parts).strip()

                # Fallback to pypdf if pdfplumber got nothing
                if not text:
                    text = _pypdf_page_text(pdf_path, page_num - 1)

                pages.append({
                    "page_number": page_num,
                    "text": text,
                    "section_heading": _detect_section_heading(text),
                })

    except Exception as e:
        # If pdfplumber fails entirely, fall back to pypdf for all pages
        print(f"[ingestion] pdfplumber error ({e}); falling back to pypdf")
        pages = _extract_all_pypdf(pdf_path)

    return pages


def _pypdf_page_text(pdf_path: str, page_index: int) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        if page_index < len(reader.pages):
            return reader.pages[page_index].extract_text() or ""
    except Exception:
        pass
    return ""


def _extract_all_pypdf(pdf_path: str) -> list[dict]:
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append({
                "page_number": i,
                "text": text,
                "section_heading": _detect_section_heading(text),
            })
        return pages
    except Exception as e:
        raise RuntimeError(f"Could not parse PDF with pypdf: {e}") from e


# ── Semantic chunker ──────────────────────────────────────────────────────────

def _word_count(text: str) -> int:
    return len(text.split())


def _chunk_text(text: str, max_words: int = None, overlap_sentences: int = 1) -> list[str]:
    """
    Split text on paragraph boundaries, then merge short paragraphs.
    Returns a list of chunk strings, each ≤ max_words words.
    """
    max_words = max_words or cfg.max_chunk_tokens

    # Split on blank lines (paragraph breaks)
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    chunks: list[str] = []
    current_parts: list[str] = []
    current_words = 0

    for para in paragraphs:
        para_words = _word_count(para)

        if current_words + para_words > max_words and current_parts:
            chunks.append("\n\n".join(current_parts))
            # Overlap: carry the last sentence of the previous chunk forward
            if overlap_sentences and current_parts:
                sentences = re.split(r"(?<=[.!?])\s+", current_parts[-1])
                overlap_text = " ".join(sentences[-overlap_sentences:])
                current_parts = [overlap_text]
                current_words = _word_count(overlap_text)
            else:
                current_parts = []
                current_words = 0

        current_parts.append(para)
        current_words += para_words

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    # If a single paragraph is longer than max_words, split by sentences
    final_chunks: list[str] = []
    for chunk in chunks:
        if _word_count(chunk) <= max_words:
            final_chunks.append(chunk)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", chunk)
            current = []
            wc = 0
            for sent in sentences:
                sw = _word_count(sent)
                if wc + sw > max_words and current:
                    final_chunks.append(" ".join(current))
                    current = []
                    wc = 0
                current.append(sent)
                wc += sw
            if current:
                final_chunks.append(" ".join(current))

    return [c for c in final_chunks if c.strip()]


# ── Document store (persisted chunk registry) ─────────────────────────────────
# We use a simple JSON file as the chunk store.  In production this would be a
# database, but a flat file is enough for demo and keeps the dependency count low.

def _load_chunk_store() -> dict[str, Any]:
    path = Path(cfg.chunk_store_path)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"documents": {}, "chunks": {}}


def _save_chunk_store(store: dict[str, Any]) -> None:
    path = Path(cfg.chunk_store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(store, f, indent=2)


# ── Re-ingest guards ──────────────────────────────────────────────────────────
# doc_id is sha256(doc_name), so re-ingesting under the same name collides.
# Chunk ids collide too, but the FAISS sidecar just APPENDS — so a silent
# re-ingest double-indexes every chunk, and FAISS has no delete to undo it.
# These make both cases explicit instead of quietly corrupting the index.

class AlreadyIndexed(Exception):
    """Same document name, byte-identical content. Nothing to do."""

    def __init__(self, doc_id: str, doc_name: str) -> None:
        self.doc_id = doc_id
        self.doc_name = doc_name
        super().__init__(f"'{doc_name}' is already indexed with identical content.")


class ContentConflict(Exception):
    """Same document name, different content — refused rather than double-indexed."""

    def __init__(self, doc_id: str, doc_name: str) -> None:
        self.doc_id = doc_id
        self.doc_name = doc_name
        super().__init__(
            f"'{doc_name}' is already indexed with different content. The vector "
            f"store cannot delete the old chunks, so re-ingesting would index this "
            f"document twice. Either ingest under a different name, or clear "
            f"{cfg.chunk_store_path} and the index and start over."
        )


def file_sha256(path: str | Path) -> str:
    """Content hash, used to tell a re-run from a changed document."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stored_pdf_path(doc_id: str) -> Path:
    return Path(cfg.pdf_store_dir) / f"{doc_id}.pdf"


def get_document(doc_id: str) -> dict[str, Any] | None:
    return _load_chunk_store()["documents"].get(doc_id)


# ── Main ingestion entry point ────────────────────────────────────────────────

def ingest_pdf(
    pdf_path: str,
    doc_name: str | None = None,
    entity: str | None = None,
    fiscal_year: str | None = None,
) -> tuple[str, list[Chunk]]:
    """
    Parse a PDF, chunk it, and return (doc_id, [Chunk, ...]).
    Callers are responsible for embedding + indexing the returned chunks.

    entity and fiscal_year are operator-supplied and applied to every chunk.
    They are not inferred from the document — see the note above
    _detect_section_heading for why detection was removed.

    basis is detected per page from section structure (app/basis.py) and may be
    None for pages outside the financial statements, which is the honest value.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc_name = doc_name or Path(pdf_path).stem
    doc_id = hashlib.sha256(doc_name.encode()).hexdigest()[:16]

    # Check for a re-ingest BEFORE doing minutes of parsing and embedding.
    content_sha = file_sha256(pdf_path)
    existing = _load_chunk_store()["documents"].get(doc_id)
    if existing:
        if existing.get("content_sha256") == content_sha:
            raise AlreadyIndexed(doc_id, doc_name)
        raise ContentConflict(doc_id, doc_name)

    pages = _extract_pages(pdf_path)
    if not pages:
        raise ValueError("No text could be extracted from this PDF.")

    # Basis needs the whole document in page order — it is a section property,
    # not a page property, so it cannot be decided one page at a time.
    page_basis = assign_basis([p["text"] for p in pages])

    chunks: list[Chunk] = []
    chunk_idx = 0

    for page, basis in zip(pages, page_basis):
        text = page["text"]
        if not text.strip():
            continue

        page_heading = page["section_heading"]

        for i, chunk_text in enumerate(_chunk_text(text)):
            chunk_id = f"{doc_id}_p{page['page_number']}_{i}"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=chunk_text,
                metadata=ChunkMetadata(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    doc_name=doc_name,
                    page_number=page["page_number"],
                    section_title=page_heading,
                    entity=entity,
                    fiscal_year=fiscal_year,
                    basis=basis,
                    chunk_index=chunk_idx,
                ),
            ))
            chunk_idx += 1

    # Keep the original PDF so citations can open their source page.  The upload
    # path used to be unlinked in a finally block, which discarded the only copy.
    stored_pdf = stored_pdf_path(doc_id)
    stored_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf_path, stored_pdf)

    # Persist doc registry
    store = _load_chunk_store()
    store["documents"][doc_id] = {
        "doc_id": doc_id,
        "doc_name": doc_name,
        "pages": len(pages),
        "chunks": len(chunks),
        "entity": entity,
        "fiscal_year": fiscal_year,
        "standalone_pages": sum(1 for b in page_basis if b == "standalone"),
        "consolidated_pages": sum(1 for b in page_basis if b == "consolidated"),
        "file_path": str(stored_pdf),
        "content_sha256": content_sha,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }
    for c in chunks:
        store["chunks"][c.chunk_id] = {
            "text": c.text,
            "metadata": c.metadata.model_dump(),
        }
    _save_chunk_store(store)

    return doc_id, chunks


def list_documents() -> list[DocumentInfo]:
    store = _load_chunk_store()
    docs = []
    for raw in store["documents"].values():
        # The stored dict carries fields DocumentInfo doesn't expose (file_path,
        # content_sha256), so filter rather than splat — and derive has_file from
        # the filesystem, not from the record, so a deleted PDF is reported
        # honestly instead of advertising a link that 404s.
        fields = {k: v for k, v in raw.items() if k in DocumentInfo.model_fields}
        path = raw.get("file_path")
        fields["has_file"] = bool(path) and Path(path).exists()
        docs.append(DocumentInfo(**fields))
    return docs


def get_chunk_by_id(chunk_id: str) -> Chunk | None:
    store = _load_chunk_store()
    entry = store["chunks"].get(chunk_id)
    if not entry:
        return None
    return Chunk(
        chunk_id=chunk_id,
        text=entry["text"],
        metadata=ChunkMetadata(**entry["metadata"]),
    )


def get_all_chunks() -> list[Chunk]:
    """Return every stored chunk — used to rebuild the BM25 index on startup."""
    store = _load_chunk_store()
    result = []
    for chunk_id, entry in store["chunks"].items():
        result.append(Chunk(
            chunk_id=chunk_id,
            text=entry["text"],
            metadata=ChunkMetadata(**entry["metadata"]),
        ))
    return result
