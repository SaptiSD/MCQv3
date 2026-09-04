"""Snapshot the live SQLite database into the Dropbox-synced data/ folder.

The live database lives outside Dropbox (SQLite and file-sync tools corrupt each
other). This writes a consistent copy back to data/mcq.db so Dropbox keeps it
backed up and versioned. Safe to run while the app is serving.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

LIVE_PATH = Path(os.getenv("MCQ_DB_PATH", Path.home() / "mcq-data" / "mcq.db"))
BACKUP_PATH = Path(__file__).parent / "data" / "mcq.db"


def snapshot(live: Path, backup: Path) -> None:
    if not live.exists():
        raise SystemExit(f"live database not found: {live}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    handle, staging_name = tempfile.mkstemp(prefix="mcq-backup-", suffix=".db")
    os.close(handle)
    staging = Path(staging_name)
    try:
        source = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(staging)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        verify = sqlite3.connect(staging)
        try:
            status = verify.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            verify.close()
        if status != "ok":
            raise SystemExit(f"refusing to publish a corrupt snapshot: {status}")
        shutil.copy2(staging, backup)
    finally:
        staging.unlink(missing_ok=True)
    print(f"{live} -> {backup} ({backup.stat().st_size} bytes, integrity ok)")


if __name__ == "__main__":
    snapshot(LIVE_PATH, BACKUP_PATH)
    sys.exit(0)
