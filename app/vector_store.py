"""
Unified vector store interface with FAISS and ChromaDB backends.

Design rationale:
- We define an abstract VectorStore base class so retrieval.py never needs to
  know which backend is active — swap via config.vector_store_backend.
- FAISS: pure numpy arrays, blazing fast, no server needed, index persisted
  to disk as a flat file.  Best for single-node, read-heavy workloads.
- Chroma: SQLite-backed, richer metadata filtering, easy to inspect.
  Better when you want to query by fiscal_year / doc_name at the store level
  rather than post-filtering in Python.
"""

from __future__ import annotations

import json
import os
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np

from app.config import cfg
from app.models import Chunk, ChunkMetadata, RetrievedChunk


# ── Abstract interface ────────────────────────────────────────────────────────

class VectorStore(ABC):
    @abstractmethod
    def add_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Index a batch of chunks with their pre-computed embeddings."""

    @abstractmethod
    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        filter_doc_name: str | None = None,
        filter_fiscal_year: str | None = None,
    ) -> list[RetrievedChunk]:
        """Return top-k chunks ranked by cosine similarity."""

    @abstractmethod
    def list_documents(self) -> list[dict[str, Any]]:
        """Return a list of {doc_id, doc_name, chunk_count} dicts."""

    @abstractmethod
    def get_chunk_count(self) -> int:
        pass


# ── FAISS backend ─────────────────────────────────────────────────────────────

class FAISSVectorStore(VectorStore):
    """
    Uses faiss.IndexFlatIP (inner-product = cosine when vectors are L2-normalised).
    Metadata is stored in a parallel JSON sidecar because FAISS only stores vectors.
    """

    def __init__(self) -> None:
        import faiss  # lazy import so Chroma users don't need faiss installed

        self._faiss = faiss
        self._dim = cfg.embedding_dim
        self._index_path = Path(cfg.faiss_index_path)
        self._meta_path = self._index_path.with_suffix(".meta.json")
        self._index_path.parent.mkdir(parents=True, exist_ok=True)

        if self._index_path.exists():
            self._index = faiss.read_index(str(self._index_path))
            with open(self._meta_path) as f:
                self._meta: list[dict] = json.load(f)
        else:
            # IndexFlatIP + normalisation = cosine similarity
            self._index = faiss.IndexFlatIP(self._dim)
            self._meta = []

    def add_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        vecs = np.array(embeddings, dtype=np.float32)
        # L2-normalise so inner product == cosine similarity
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        vecs = vecs / norms

        self._index.add(vecs)
        for chunk in chunks:
            self._meta.append({
                "chunk_id": chunk.chunk_id,
                "text": chunk.text,
                "metadata": chunk.metadata.model_dump(),
            })
        self._save()

    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        filter_doc_name: str | None = None,
        filter_fiscal_year: str | None = None,
    ) -> list[RetrievedChunk]:
        if self._index.ntotal == 0:
            return []

        qvec = np.array([query_embedding], dtype=np.float32)
        norm = np.linalg.norm(qvec)
        if norm > 0:
            qvec = qvec / norm

        # Fetch extra candidates to allow for post-filter attrition
        fetch_k = min(top_k * 5, self._index.ntotal)
        scores, indices = self._index.search(qvec, fetch_k)

        results: list[RetrievedChunk] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue
            entry = self._meta[idx]
            meta = entry["metadata"]

            if filter_doc_name and meta.get("doc_name") != filter_doc_name:
                continue
            if filter_fiscal_year and meta.get("fiscal_year") != filter_fiscal_year:
                continue

            results.append(RetrievedChunk(
                chunk=Chunk(
                    chunk_id=entry["chunk_id"],
                    text=entry["text"],
                    metadata=ChunkMetadata(**meta),
                ),
                vector_score=float(score),
                retrieval_source="vector",
            ))
            if len(results) >= top_k:
                break

        return results

    def list_documents(self) -> list[dict[str, Any]]:
        docs: dict[str, dict] = {}
        for entry in self._meta:
            m = entry["metadata"]
            did = m["doc_id"]
            if did not in docs:
                docs[did] = {
                    "doc_id": did,
                    "doc_name": m["doc_name"],
                    "fiscal_year": m.get("fiscal_year"),
                    "chunk_count": 0,
                }
            docs[did]["chunk_count"] += 1
        return list(docs.values())

    def get_chunk_count(self) -> int:
        return self._index.ntotal

    def _save(self) -> None:
        self._faiss.write_index(self._index, str(self._index_path))
        with open(self._meta_path, "w") as f:
            json.dump(self._meta, f)


# ── ChromaDB backend ──────────────────────────────────────────────────────────

def _scalar_metadata(metadata: ChunkMetadata) -> dict[str, Any]:
    """
    Chunk metadata reduced to what Chroma will accept.

    Chroma stores only str/int/float/bool and rejects None outright, but four
    fields on ChunkMetadata are legitimately nullable — section_title, entity,
    fiscal_year and basis.  `basis` is null for every page outside the statement
    blocks (83+ pages in the demo corpus alone), so passing model_dump() straight
    through fails on the very first write.

    Dropping the keys is safe rather than lossy: all four default to None on
    ChunkMetadata, so the absent key is restored as None when search() rebuilds
    the object.  The alternative — writing a sentinel like "" or "unknown" —
    would round-trip an undetermined basis into a determined-looking one, which
    is exactly the confusion app/basis.py exists to prevent.
    """
    return {k: v for k, v in metadata.model_dump().items() if v is not None}

class ChromaVectorStore(VectorStore):
    """
    Uses chromadb's persistent client with a custom embedding function that wraps
    our sentence-transformer model.  Chroma handles its own storage so no sidecar
    files are needed — metadata filtering happens inside the DB query.
    """

    def __init__(self) -> None:
        import chromadb  # lazy import

        if cfg.chroma_is_cloud:
            # Managed service.  Selected by the presence of an API key rather
            # than a separate mode flag, so there is no state where a key is
            # configured but quietly ignored.  config.validate() has already
            # refused a key without a tenant and database.
            self._client = chromadb.CloudClient(
                api_key=cfg.chroma_api_key,
                tenant=cfg.chroma_tenant,
                database=cfg.chroma_database,
            )
        else:
            persist_dir = cfg.chroma_persist_dir
            Path(persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=persist_dir)

        self._collection = self._client.get_or_create_collection(
            name="financial_chunks",
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        self._collection.add(
            ids=[c.chunk_id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[_scalar_metadata(c.metadata) for c in chunks],
        )

    def search(
        self,
        query_embedding: list[float],
        top_k: int,
        filter_doc_name: str | None = None,
        filter_fiscal_year: str | None = None,
    ) -> list[RetrievedChunk]:
        where: dict[str, Any] = {}
        if filter_doc_name:
            where["doc_name"] = filter_doc_name
        if filter_fiscal_year:
            where["fiscal_year"] = filter_fiscal_year

        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(top_k, max(1, self._collection.count())),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            results = self._collection.query(**kwargs)
        except Exception:
            return []

        retrieved: list[RetrievedChunk] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # Chroma returns cosine distance (0=identical); convert to similarity
            similarity = 1.0 - dist
            retrieved.append(RetrievedChunk(
                chunk=Chunk(
                    chunk_id=meta["chunk_id"],
                    text=doc,
                    metadata=ChunkMetadata(**meta),
                ),
                vector_score=similarity,
                retrieval_source="vector",
            ))
        return retrieved

    def list_documents(self) -> list[dict[str, Any]]:
        if self._collection.count() == 0:
            return []
        all_meta = self._collection.get(include=["metadatas"])["metadatas"]
        docs: dict[str, dict] = {}
        for m in all_meta:
            did = m["doc_id"]
            if did not in docs:
                docs[did] = {
                    "doc_id": did,
                    "doc_name": m["doc_name"],
                    "fiscal_year": m.get("fiscal_year"),
                    "chunk_count": 0,
                }
            docs[did]["chunk_count"] += 1
        return list(docs.values())

    def get_chunk_count(self) -> int:
        return self._collection.count()


# ── Factory ───────────────────────────────────────────────────────────────────

def get_vector_store() -> VectorStore:
    """Return the configured vector store singleton."""
    backend = cfg.vector_store_backend.lower()
    if backend == "chroma":
        return ChromaVectorStore()
    return FAISSVectorStore()
