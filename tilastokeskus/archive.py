"""Raw response archive (D-20).

Every response is written to disk, gzipped, before anything parses it. A parsing bug then costs a
re-parse rather than a re-fetch, and a shape change is diffable against what came before.

Layout: ``{root}/{UTC date}/{UTC HHMMSS}/{resource}/{key}.json.gz`` — one directory per run, so a
second run on the same day never overwrites the first. ``tilasto purge --data`` removes the whole
root (D-50).
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, datetime
from pathlib import Path


class RawArchive:
    """Writes one run's responses under a directory named for when the run started."""

    def __init__(self, root: Path, started_at: datetime) -> None:
        if started_at.tzinfo is None:
            # A naive time would file the run under the host's local date, not UTC.
            raise ValueError("started_at must be timezone-aware")
        stamp = started_at.astimezone(UTC)
        self.run_dir = Path(root) / stamp.strftime("%Y-%m-%d") / stamp.strftime("%H%M%S")

    def write(self, resource: str, key: str, payload: object) -> Path:
        """Archive ``payload`` and return the path written. Never overwrites."""
        for label, value in (("resource", resource), ("key", key)):
            if not value or value in {".", ".."} or "/" in value or os.sep in value:
                raise ValueError(f"invalid archive {label}: {value!r}")

        path = self.run_dir / resource / f"{key}.json.gz"
        if path.exists():
            raise FileExistsError(f"refusing to overwrite archived response {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

        # Written under a temporary name and renamed, so a crash cannot leave a truncated file
        # that looks like a complete response.
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as fh:
                json.dump(payload, fh)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return path
