"""
Hybrid retrieval pipeline: vector search + BM25 → merge → cross-encoder rerank.

Architecture overview
─────────────────────
1. EmbeddingModel   — wraps sentence-transformers, encodes queries + chunks.
2. BM25Index        — wraps rank_bm25.BM25Okapi; built from tokenised chunks.
                      Rebuilt from the chunk store at startup, serialised to disk.
3. Reranker         — wraps CrossEncoder; scores (query, chunk) pairs.
4. HybridRetriever  — orchestrates steps 1-3, merges + dedupes results from
                      vector search and BM25, calls the reranker on the merged
                      candidate set, returns final_top_n best chunks.

Why parallel retrieval matters for financial documents
──────────────────────────────────────────────────────
Vector search captures semantic similarity ("what did revenue do?"), but dense
embeddings compress information and can lose exact numbers and table values.
BM25 keyword search reliably surfaces chunks that *literally contain* the query
terms (ticker symbols, line-item names like "EBITDA", specific dollar figures).
Running both and reranking the union gives coverage that neither method alone
achieves — especially critical for tables and numerical footnotes.

No orchestration framework:
  The pipeline below is explicit — embed, search two ways, merge, rerank.  That
  is short enough to read end to end, and the parts worth protecting (provenance
  in the indexed text, the abstention gate in app/confidence.py) are not things a
  chain abstraction models.  EmbeddingModel does expose embed_documents and
  embed_query, so it duck-types into a LangChain chain if one is ever wanted,
  without the dependency.
"""

from __future__ import annotations

import os
import pickle
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np

from app.config import cfg
from app.models import Chunk, RetrievedChunk


# ── Embedding model ───────────────────────────────────────────────────────────

class EmbeddingModel:
    """
    Thin wrapper around sentence-transformers.SentenceTransformer.

    embed_documents / embed_query match the shape LangChain's Embeddings
    interface expects, so this duck-types into a chain without inheriting from
    it or importing anything.  It is not a subclass and does not claim to be.
    """

    def __init__(self, model_name: str = None) -> None:
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name or cfg.embedding_model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, convert_to_numpy=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text], convert_to_numpy=True)[0].tolist()


# ── BM25 index ────────────────────────────────────────────────────────────────

_TOKENISE = re.compile(r"\b\w+\b")


def _tokenise(text: str) -> list[str]:
    return _TOKENISE.findall(text.lower())


class BM25Index:
    """
    Wraps rank_bm25.BM25Okapi.
    chunk_ids and tokenised_corpus are stored in parallel lists so we can map
    a BM25 rank back to a chunk_id for merging with vector results.
    """

    def __init__(self) -> None:
        self._chunk_ids: list[str] = []
        self._chunks: list[Chunk] = []
        self._bm25 = None
        self._lock = threading.Lock()

    def build(self, chunks: list[Chunk]) -> None:
        # BM25Plus instead of BM25Okapi: BM25Plus adds a lower-bound term-frequency
        # weight that keeps scores positive even when a term appears in exactly half
        # the corpus (where BM25Okapi collapses to IDF=0 due to log(1)=0).
        from rank_bm25 import BM25Plus

        with self._lock:
            self._chunk_ids = [c.chunk_id for c in chunks]
            self._chunks = chunks
            # indexed_text, not text: it prepends entity, fiscal year and basis,
            # which is what lets a query naming a basis reach the right set of
            # statements. Without it the consolidated P&L for a revenue query
            # sat at BM25 rank 118. See Chunk.indexed_text.
            corpus = [_tokenise(c.indexed_text) for c in chunks]
            self._bm25 = BM25Plus(corpus)

    def add_chunks(self, chunks: list[Chunk]) -> None:
        """Incrementally add chunks — rebuilds the index (BM25Okapi is immutable)."""
        all_chunks = self._chunks + chunks
        self.build(all_chunks)

    def search(
        self,
        query: str,
        top_k: int,
        filter_doc_name: str | None = None,
        filter_fiscal_year: str | None = None,
    ) -> list[RetrievedChunk]:
        if not self._bm25 or not self._chunk_ids:
            return []

        with self._lock:
            tokens = _tokenise(query)
            scores = self._bm25.get_scores(tokens)

        # Pair (score, index) and sort descending
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results: list[RetrievedChunk] = []
        for idx, score in ranked:
            if score <= 0:
                # BM25Okapi gives 0 when a term appears in ALL documents (IDF=0).
                # Use continue (not break) so filters can still find relevant chunks
                # that happen to share vocabulary with the entire corpus.
                continue
            chunk = self._chunks[idx]
            if filter_doc_name and chunk.metadata.doc_name != filter_doc_name:
                continue
            if filter_fiscal_year and chunk.metadata.fiscal_year != filter_fiscal_year:
                continue
            results.append(RetrievedChunk(
                chunk=chunk,
                bm25_score=float(score),
                retrieval_source="bm25",
            ))
            if len(results) >= top_k:
                break
        return results

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "chunk_ids": self._chunk_ids,
                "chunks": [(c.chunk_id, c.text, c.metadata.model_dump()) for c in self._chunks],
            }, f)

    def load(self, path: str) -> bool:
        """Load persisted token lists and rebuild BM25Plus in-memory. Returns True on success."""
        from app.models import ChunkMetadata

        if not Path(path).exists():
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            chunks = [
                Chunk(chunk_id=cid, text=text, metadata=ChunkMetadata(**meta))
                for cid, text, meta in data["chunks"]
            ]
            self.build(chunks)
            return True
        except Exception as e:
            print(f"[BM25] Failed to load index: {e}")
            return False


# ── Cross-encoder reranker ────────────────────────────────────────────────────

class Reranker:
    """
    Wraps sentence-transformers CrossEncoder.
    Scores each (query, chunk_text) pair; higher score = more relevant.
    The cross-encoder sees the full query + passage together (unlike bi-encoder
    embeddings that encode them separately), so it's much more accurate — at
    the cost of being slower.  That's why we only rerank a small merged candidate
    set, not the entire corpus.
    """

    def __init__(self, model_name: str = None) -> None:
        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(model_name or cfg.reranker_model)

    def rerank(self, query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        if not candidates:
            return []
        # indexed_text, matching what the embedder and BM25 were given. Scoring
        # the bare text here silently undid the provenance line: the cross-encoder
        # is the component that decides the final order, so a chunk whose entity,
        # year and basis it cannot see loses to a narrative page that merely talks
        # about the topic in prose.
        pairs = [(query, c.chunk.indexed_text) for c in candidates]
        scores = self._model.predict(pairs).tolist()
        for candidate, score in zip(candidates, scores):
            candidate.rerank_score = float(score)
        return sorted(candidates, key=lambda c: c.rerank_score or 0, reverse=True)


# ── Hybrid retriever ──────────────────────────────────────────────────────────

class HybridRetriever:
    """
    Combines vector search + BM25 keyword search, deduplicates by chunk_id,
    then reranks the merged set with a cross-encoder.

    Workflow per query:
      1. Embed the query with the bi-encoder.
      2. Vector search: top cfg.vector_top_k chunks by cosine similarity.
      3. BM25 search:   top cfg.bm25_top_k chunks by BM25Okapi score.
      4. Merge + deduplicate on chunk_id; prefer the entry that already has a
         score in both retrieval modes (mark retrieval_source="hybrid").
      5. Cross-encoder rerank the merged candidate set.
      6. Return final_top_n chunks with all three scores attached.
    """

    def __init__(
        self,
        vector_store,        # VectorStore instance (FAISS or Chroma)
        bm25_index: BM25Index,
        embedding_model: EmbeddingModel,
        reranker: Reranker,
    ) -> None:
        self._vs = vector_store
        self._bm25 = bm25_index
        self._embed = embedding_model
        self._reranker = reranker

    def retrieve(
        self,
        query: str,
        top_n: int | None = None,
        filter_doc_name: str | None = None,
        filter_fiscal_year: str | None = None,
        filter_section_type: str | None = None,
    ) -> list[RetrievedChunk]:
        top_n = top_n or cfg.final_top_n

        # Step 1 — embed query
        q_embedding = self._embed.embed_query(query)

        # Step 2 — vector search
        vector_results = self._vs.search(
            q_embedding,
            top_k=cfg.vector_top_k,
            filter_doc_name=filter_doc_name,
            filter_fiscal_year=filter_fiscal_year,
        )

        # Step 3 — BM25 search
        bm25_results = self._bm25.search(
            query,
            top_k=cfg.bm25_top_k,
            filter_doc_name=filter_doc_name,
            filter_fiscal_year=filter_fiscal_year,
        )

        # Step 4 — merge + dedupe
        merged: dict[str, RetrievedChunk] = {}

        for r in vector_results:
            merged[r.chunk.chunk_id] = r

        for r in bm25_results:
            cid = r.chunk.chunk_id
            if cid in merged:
                # Already found by vector search — add BM25 score and mark hybrid
                merged[cid].bm25_score = r.bm25_score
                merged[cid].retrieval_source = "hybrid"
            else:
                merged[cid] = r

        candidates = list(merged.values())

        # Optional section-type filter (applied in Python since BM25/FAISS don't
        # support it natively without custom metadata indexes)
        if filter_section_type:
            ft = filter_section_type.lower()
            candidates = [
                c for c in candidates
                if c.chunk.metadata.section_title
                and ft in c.chunk.metadata.section_title.lower()
            ]

        # Step 5 — cross-encoder rerank
        reranked = self._reranker.rerank(query, candidates)

        return reranked[:top_n]
