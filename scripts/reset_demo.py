"""
Reset the demo to a clean cold-start state, and say whether it is safe to
present.

What it wipes: runtime state only — reconciliation runs, matches, exceptions,
decisions, notice replies, run logs. What it keeps: the ingested corpus (chunk
store, FAISS index, stored PDFs) and the seed data it re-applies (clients,
deadlines). Idempotent; run it as many times as you like.

    python -m scripts.reset_demo

Exit code 0 means every go/no-go check passed.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# .env must be in the environment BEFORE app.config is imported — the config
# singleton reads os.getenv at import. In the server this happens in main.py;
# a standalone script has to do it itself or the provider check reads "none"
# with a perfectly good key sitting in .env.
from dotenv import load_dotenv                  # noqa: E402
load_dotenv()

from app.config import cfg                      # noqa: E402
from app.runlog import RUNS_DIR                 # noqa: E402
from app.structured import connect, seed_from_dataset  # noqa: E402

RUNTIME_TABLES = ("reco_runs", "reco_matches", "exceptions", "approvals",
                  "notice_replies")


def wipe_runtime_state() -> None:
    with connect() as conn:
        for table in RUNTIME_TABLES:
            conn.execute(f"DELETE FROM {table}")
    if RUNS_DIR.exists():
        shutil.rmtree(RUNS_DIR)


def checks() -> list[tuple[bool, str, str]]:
    out: list[tuple[bool, str, str]] = []

    def check(ok: bool, name: str, detail: str = "") -> None:
        out.append((bool(ok), name, detail))

    # Corpus intact and matching the manifest.
    manifest_path = Path("data/ca_dataset/manifest.json")
    store_path = Path(cfg.chunk_store_path)
    check(manifest_path.exists(), "dataset manifest present")
    check(store_path.exists(), "chunk store present")
    if manifest_path.exists() and store_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        store = json.loads(store_path.read_text(encoding="utf-8"))
        docs = store.get("documents", {})
        check(len(docs) == len(manifest),
              "indexed documents match the manifest",
              f"{len(docs)}/{len(manifest)}")
        missing_pdfs = [d["doc_name"] for d in docs.values()
                        if not Path(d.get("file_path", "")).exists()]
        check(not missing_pdfs, "every stored PDF is on disk",
              "; ".join(missing_pdfs[:3]))

    faiss_path = Path(cfg.faiss_index_path)
    check(faiss_path.exists(), "FAISS index present")

    # Structured store seeded, runtime tables empty.
    with connect() as conn:
        clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        check(clients >= 2, "clients seeded", str(clients))
        for table in RUNTIME_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            check(count == 0, f"{table} empty", str(count))

    # The recon inputs the demo runs against.
    for name in ("mehta_purchase_register_2025-12.csv",
                 "mehta_gstr2b_2025-12.csv",
                 "asmt10_mehta.pdf",
                 "fallback_reply.md"):
        check(Path("data/ca_dataset", name).exists(), f"dataset file {name}")

    # A generation key is configured (silently extractive answers are the
    # worst demo failure — surface it here, not on stage).
    check(cfg.generation_provider != "none", "generation provider configured",
          cfg.generation_provider)
    return out


def main() -> int:
    print("wiping runtime state (runs, exceptions, decisions, replies, logs)")
    wipe_runtime_state()
    print("re-seeding clients and deadlines")
    counts = seed_from_dataset()
    print(f"  seeded: {counts}")

    print("\ngo/no-go checklist")
    results = checks()
    for ok, name, detail in results:
        print("  %s  %-42s %s" % ("PASS" if ok else "FAIL", name, detail))

    failed = [r for r in results if not r[0]]
    if failed:
        print(f"\nNOT ready: {len(failed)} check(s) failed.")
        return 1
    print("\nClean state. Start the server and run scripts/smoke_ui.py before "
          "presenting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
