"""
The Chroma backend's two failure modes, both of which shipped broken.

Neither is exercised by the FAISS path, and both fail at write time against a
real service — so they get unit tests with a stubbed chromadb rather than being
discovered during a reindex.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.config import Config, cfg
from app.models import Chunk, ChunkMetadata
from app.vector_store import _scalar_metadata


def _meta(**overrides) -> ChunkMetadata:
    base = dict(
        chunk_id="c1", doc_id="d1", doc_name="Infosys FY2024-25",
        page_number=30, chunk_index=0,
    )
    base.update(overrides)
    return ChunkMetadata(**base)


# ── Null metadata ─────────────────────────────────────────────────────────────

def test_none_values_are_dropped():
    """
    Chroma stores only str/int/float/bool and rejects None outright. `basis` is
    null for every page outside the statement blocks - 83+ pages in the demo
    corpus - so passing model_dump() straight through fails on the first write.
    """
    out = _scalar_metadata(_meta(basis=None, entity=None, fiscal_year=None,
                                 section_title=None))

    assert "basis" not in out
    assert "entity" not in out
    assert "fiscal_year" not in out
    assert "section_title" not in out
    # The non-null fields must survive intact.
    assert out["chunk_id"] == "c1"
    assert out["page_number"] == 30


def test_present_values_are_kept():
    out = _scalar_metadata(_meta(basis="consolidated", entity="Infosys",
                                 fiscal_year="FY2024-25"))
    assert out["basis"] == "consolidated"
    assert out["entity"] == "Infosys"
    assert out["fiscal_year"] == "FY2024-25"


def test_every_surviving_value_is_a_chroma_scalar():
    out = _scalar_metadata(_meta(basis="standalone", entity="TCS",
                                 fiscal_year="FY2024-25",
                                 section_title="Notes to accounts"))
    for key, value in out.items():
        assert isinstance(value, (str, int, float, bool)), f"{key}={value!r}"


def test_dropped_keys_round_trip_back_to_none():
    """
    Dropping is safe rather than lossy: the nullable fields all default to None
    on ChunkMetadata, so search() reconstructs the original object. Writing a
    sentinel instead would turn an undetermined basis into a determined-looking
    one - the confusion app/basis.py exists to prevent.
    """
    original = _meta(basis=None, entity=None, fiscal_year=None, section_title=None)

    restored = ChunkMetadata(**_scalar_metadata(original))

    assert restored == original
    assert restored.basis is None


def test_a_falsy_but_real_value_is_not_dropped():
    """Filtering on `is not None`, not on truthiness - page 0 must survive."""
    out = _scalar_metadata(_meta(page_number=0, chunk_index=0))
    assert out["page_number"] == 0
    assert out["chunk_index"] == 0


# ── Cloud vs local client selection ───────────────────────────────────────────

@pytest.fixture
def stub_chromadb(monkeypatch):
    """Install a fake chromadb module and record which client was constructed."""
    calls = {}

    class _Collection:
        def count(self):
            return 0

    class _Client:
        def get_or_create_collection(self, **kwargs):
            return _Collection()

    def cloud_client(**kwargs):
        calls["kind"] = "cloud"
        calls["kwargs"] = kwargs
        return _Client()

    def persistent_client(**kwargs):
        calls["kind"] = "local"
        calls["kwargs"] = kwargs
        return _Client()

    module = types.ModuleType("chromadb")
    module.CloudClient = cloud_client
    module.PersistentClient = persistent_client
    monkeypatch.setitem(sys.modules, "chromadb", module)
    return calls


def test_api_key_selects_the_cloud_client(monkeypatch, stub_chromadb):
    monkeypatch.setattr(cfg, "chroma_api_key", "ck-test")
    monkeypatch.setattr(cfg, "chroma_tenant", "tenant-1")
    monkeypatch.setattr(cfg, "chroma_database", "ragsample")

    from app.vector_store import ChromaVectorStore
    ChromaVectorStore()

    assert stub_chromadb["kind"] == "cloud"
    assert stub_chromadb["kwargs"]["tenant"] == "tenant-1"
    assert stub_chromadb["kwargs"]["database"] == "ragsample"


def test_no_api_key_stays_local(monkeypatch, stub_chromadb, tmp_path):
    """A checkout with no credentials must still run."""
    monkeypatch.setattr(cfg, "chroma_api_key", None)
    monkeypatch.setattr(cfg, "chroma_persist_dir", str(tmp_path / "chroma"))

    from app.vector_store import ChromaVectorStore
    ChromaVectorStore()

    assert stub_chromadb["kind"] == "local"


# ── Config validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("tenant,database,expected", [
    (None, "ragsample", "CHROMA_TENANT"),
    ("t", None, "CHROMA_DATABASE"),
    (None, None, "CHROMA_TENANT"),
])
def test_key_without_tenant_or_database_is_refused(monkeypatch, tenant, database,
                                                   expected):
    """
    An incomplete cloud config does not degrade to local Chroma - it fails at the
    first query, by which point the service looks up and healthy. Refuse at
    startup and name the missing variable.
    """
    monkeypatch.setenv("CHROMA_API_KEY", "ck-test")
    if tenant:
        monkeypatch.setenv("CHROMA_TENANT", tenant)
    else:
        monkeypatch.delenv("CHROMA_TENANT", raising=False)
    if database:
        monkeypatch.setenv("CHROMA_DATABASE", database)
    else:
        monkeypatch.delenv("CHROMA_DATABASE", raising=False)

    with pytest.raises(ValueError, match=expected):
        Config().validate()


def test_complete_cloud_config_validates(monkeypatch):
    monkeypatch.setenv("CHROMA_API_KEY", "ck-test")
    monkeypatch.setenv("CHROMA_TENANT", "t")
    monkeypatch.setenv("CHROMA_DATABASE", "d")

    Config().validate()  # must not raise


def test_no_key_needs_no_tenant(monkeypatch):
    """Local Chroma and FAISS must not be dragged into cloud validation."""
    monkeypatch.delenv("CHROMA_API_KEY", raising=False)
    monkeypatch.delenv("CHROMA_TENANT", raising=False)
    monkeypatch.delenv("CHROMA_DATABASE", raising=False)

    c = Config()
    c.validate()
    assert c.chroma_is_cloud is False


def test_chroma_is_cloud_tracks_the_key(monkeypatch):
    monkeypatch.setenv("CHROMA_API_KEY", "ck-test")
    monkeypatch.setenv("CHROMA_TENANT", "t")
    monkeypatch.setenv("CHROMA_DATABASE", "d")
    assert Config().chroma_is_cloud is True


def test_blank_env_key_reads_as_absent(monkeypatch):
    """
    An empty CHROMA_API_KEY= line in .env is "not configured", not a key of
    length zero - otherwise commenting a key out by blanking it would trip the
    tenant/database validation.
    """
    monkeypatch.setenv("CHROMA_API_KEY", "")
    c = Config()
    assert c.chroma_api_key is None
    assert c.chroma_is_cloud is False
    c.validate()  # must not raise
