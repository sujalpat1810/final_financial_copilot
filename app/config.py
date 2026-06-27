"""
Central configuration for the Financial Research Copilot.
All tunable knobs live here; environment variables override defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Config:
    # ── Vector store backend ──────────────────────────────────────────────────
    # Switch between "faiss" and "chroma" without changing any other code.
    # FAISS is purely in-memory/on-disk (faster for small corpora);
    # Chroma persists to SQLite and supports richer metadata filtering.
    vector_store_backend: Literal["faiss", "chroma"] = field(
        default_factory=lambda: os.getenv("VECTOR_STORE_BACKEND", "faiss")
    )

    # ── Paths ─────────────────────────────────────────────────────────────────
    faiss_index_path: str = field(
        default_factory=lambda: os.getenv("FAISS_INDEX_PATH", "data/faiss_index")
    )
    chroma_persist_dir: str = field(
        default_factory=lambda: os.getenv("CHROMA_PERSIST_DIR", "data/chroma_db")
    )
    bm25_index_path: str = field(
        default_factory=lambda: os.getenv("BM25_INDEX_PATH", "data/bm25_index.pkl")
    )
    chunk_store_path: str = field(
        default_factory=lambda: os.getenv("CHUNK_STORE_PATH", "data/chunk_store.json")
    )

    # ── Embedding model (sentence-transformers, local, no API key needed) ─────
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    )
    embedding_dim: int = 384  # matches all-MiniLM-L6-v2 output dimension

    # ── Cross-encoder reranker (also local, no API key needed) ────────────────
    reranker_model: str = field(
        default_factory=lambda: os.getenv(
            "RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
    )

    # ── Retrieval hyperparameters ─────────────────────────────────────────────
    vector_top_k: int = int(os.getenv("VECTOR_TOP_K", "10"))   # candidates from vector search
    bm25_top_k: int = int(os.getenv("BM25_TOP_K", "10"))       # candidates from BM25 search
    final_top_n: int = int(os.getenv("FINAL_TOP_N", "5"))      # chunks sent to the LLM

    # ── Chunking parameters ───────────────────────────────────────────────────
    max_chunk_tokens: int = int(os.getenv("MAX_CHUNK_TOKENS", "400"))
    chunk_overlap_sentences: int = int(os.getenv("CHUNK_OVERLAP_SENTENCES", "1"))

    # ── Generation ───────────────────────────────────────────────────────────
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")


# Singleton — import this everywhere instead of constructing a new Config each time.
cfg = Config()
