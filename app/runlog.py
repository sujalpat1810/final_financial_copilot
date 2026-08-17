"""
Append-only run logs — the working-paper trail.

One JSONL file per run under data/runs/.  Events are appended and never
rewritten, so "immutable" is a property of the format, not a promise: the file
IS the export, downloadable as-is.  Each event records what stage ran, what it
saw, what it produced, and (for LLM steps) which model and prompt version —
everything a reviewer needs to reconstruct how a conclusion was reached.

ICAI framing: members remain personally accountable for work AI assists with,
and an exportable, timestamped record of every step is how a firm evidences
due diligence over that work.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNS_DIR = Path("data/runs")


class RunLog:
    """Append-only JSONL writer for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        self.path = RUNS_DIR / f"{run_id}.jsonl"

    def event(self, stage: str, detail: str = "", *, counts: dict | None = None,
              model: str | None = None, prompt_version: str | None = None,
              **extra: Any) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "stage": stage,
        }
        if detail:
            record["detail"] = detail
        if counts:
            record["counts"] = counts
        if model:
            record["model"] = model
        if prompt_version:
            record["prompt_version"] = prompt_version
        record.update(extra)
        # Opened per event: an append is atomic enough at this scale, and a
        # held-open handle would survive past the run's lifetime.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_text(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""
