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

    # ── Chroma Cloud ──────────────────────────────────────────────────────────
    # Setting an API key switches the chroma backend from a local on-disk client
    # to the managed service; tenant and database identify which one.  Leaving
    # the key unset keeps chroma purely local, so a checkout with no credentials
    # still runs.  All three are required together — see validate().
    chroma_api_key: str | None = field(
        default_factory=lambda: os.getenv("CHROMA_API_KEY") or None
    )
    chroma_tenant: str | None = field(
        default_factory=lambda: os.getenv("CHROMA_TENANT") or None
    )
    chroma_database: str | None = field(
        default_factory=lambda: os.getenv("CHROMA_DATABASE") or None
    )

    @property
    def chroma_is_cloud(self) -> bool:
        """Whether the chroma backend should talk to the managed service."""
        return bool(self.chroma_api_key)
    bm25_index_path: str = field(
        default_factory=lambda: os.getenv("BM25_INDEX_PATH", "data/bm25_index.pkl")
    )
    chunk_store_path: str = field(
        default_factory=lambda: os.getenv("CHUNK_STORE_PATH", "data/chunk_store.json")
    )
    # Where ingested PDFs are kept so citations can open their source page.
    # Deliberately NOT pdf_data/ — that is the operator's source corpus and stays
    # untouched.  Served copies are duplicated (~30 MB per report) so a moved or
    # deleted source file can never break the viewer mid-demo.
    pdf_store_dir: str = field(
        default_factory=lambda: os.getenv("PDF_STORE_DIR", "data/pdf_store")
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
    # gemini-2.5-flash is retired for new API keys: it is still returned by
    # models.list() but generate_content answers 404 "no longer available to new
    # users".  generate_answer catches that and falls back to extractive, so a
    # stale default here costs every generated answer without raising anything.
    gemini_model: str = field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    )
    # default_factory for the same reason as the thresholds below: a bare
    # os.getenv() default is evaluated at import, so it would miss a key that
    # load_dotenv() puts in the environment after this module is first imported.
    gemini_api_key: str | None = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY")
    )

    # ── Confidence + abstention thresholds ────────────────────────────────────
    # These are RAW CROSS-ENCODER LOGITS, not probabilities.  The reranker
    # (cross-encoder/ms-marco-MiniLM-L-6-v2) emits unbounded scores, roughly
    # -11..+11.  Sigmoid saturates hard at both ends — irrelevant passages land
    # near 0.00002 and good ones near 0.9997 — so thresholding on a squashed
    # probability would put every decision boundary inside a rounding error.
    # Compare logits directly instead.
    #
    # The defaults below are a STARTING POINT, not calibrated values.  The model
    # is trained on MS MARCO web passages; its behaviour on dense Ind AS tables
    # has to be measured.  scripts/calibrate.py dumps the score distribution over
    # the demo corpus so these can be set from data.  Override via env.
    #
    # default_factory, not a bare default: a plain `float(os.getenv(...))` default
    # is evaluated once when this module is imported, so constructing a second
    # Config would silently ignore the environment.  The path fields above use
    # default_factory for the same reason.
    rerank_high_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_HIGH_THRESHOLD", "2.0"))
    )
    rerank_moderate_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_MODERATE_THRESHOLD", "-2.0"))
    )
    rerank_abstain_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_ABSTAIN_THRESHOLD", "-6.0"))
    )

    # Supporting chunks at or above the moderate threshold needed for "high".
    # One strong match is a single point of failure; agreement is what makes it
    # high confidence.
    confidence_min_supporting: int = field(
        default_factory=lambda: int(os.getenv("CONFIDENCE_MIN_SUPPORTING", "2"))
    )

    def validate(self) -> None:
        """
        Fail loudly on a mis-ordered threshold set.

        Raising at import is harsh, but a silently inverted set produces
        confidence labels that are meaningless while still looking authoritative
        — the exact failure this product exists to prevent.  Better to refuse to
        start than to mislabel every answer.
        """
        if not (self.rerank_high_threshold
                > self.rerank_moderate_threshold
                > self.rerank_abstain_threshold):
            raise ValueError(
                "Confidence thresholds must satisfy "
                "RERANK_HIGH_THRESHOLD > RERANK_MODERATE_THRESHOLD > "
                f"RERANK_ABSTAIN_THRESHOLD; got {self.rerank_high_threshold}, "
                f"{self.rerank_moderate_threshold}, {self.rerank_abstain_threshold}."
            )
        if self.confidence_min_supporting < 1:
            raise ValueError(
                "CONFIDENCE_MIN_SUPPORTING must be >= 1; got "
                f"{self.confidence_min_supporting}."
            )
        # A key without a tenant or database does not degrade to local Chroma —
        # it fails at the first query, by which point the service looks up and
        # healthy.  Refuse at startup instead, naming the missing variable.
        if self.chroma_api_key:
            missing = [
                name for name, value in (
                    ("CHROMA_TENANT", self.chroma_tenant),
                    ("CHROMA_DATABASE", self.chroma_database),
                ) if not value
            ]
            if missing:
                raise ValueError(
                    "CHROMA_API_KEY is set, so Chroma Cloud is selected, but "
                    f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} "
                    "missing. Find both on the Chroma Cloud dashboard."
                )


# Singleton — import this everywhere instead of constructing a new Config each time.
cfg = Config()
cfg.validate()
