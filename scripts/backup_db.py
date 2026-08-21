"""
Snapshot the local SQLite database (crypto_data.db) into backups/.

Uses SQLite's online backup API (Connection.backup), so the snapshot is
consistent even while the collector / live server is writing to the DB —
no "database is locked" errors, and no risk of copying a torn WAL state.

Keeps only the newest `KEEP` snapshots and prunes the rest.

Usage:
    python scripts/backup_db.py [keep]     # keep defaults to 10
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "crypto_data.db"


def backup_db(db_path: Path, backup_dir: Path, keep: int = 10) -> Path:
    """Copy db_path to backup_dir with a timestamp; prune old snapshots."""
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Clean up any half-written snapshots left by an interrupted run.
    for stray in backup_dir.glob("tmp*.db"):
        stray.unlink(missing_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_dir / f"crypto_data_{stamp}.db"

    # Write to a temp file in the same dir, then atomically move it into
    # place — so an interrupted backup never leaves a half-written snapshot.
    fd, tmp_name = tempfile.mkstemp(dir=str(backup_dir), suffix=".db")
    os.close(fd)  # mkstemp leaves an open handle; release it right away

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp_name)
    try:
        # Connection.backup() produces a consistent snapshot even while the
        # collector is writing (WAL mode) — no "database is locked" errors.
        src.backup(dst)
    finally:
        # NB: `with sqlite3.connect(...)` only commits/rolls back — it does
        # NOT close the connection. Close explicitly or the temp file stays
        # locked and the rename below fails on Windows.
        dst.close()
        src.close()

    # Atomic move into place.
    shutil.move(tmp_name, dest)

    # Prune old snapshots, keep the newest `keep`.
    snapshots = sorted(backup_dir.glob("crypto_data_*.db"))
    for old in snapshots[:-keep]:
        old.unlink(missing_ok=True)

    return dest


def main() -> None:
    try:
        keep = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    except ValueError:
        print("Usage: python scripts/backup_db.py [keep]")
        sys.exit(2)

    if not DB_PATH.exists():
        print(f"No database found at {DB_PATH} — nothing to back up.")
        sys.exit(1)

    dest = backup_db(DB_PATH, BACKUP_DIR, keep=keep)
    size_mb = dest.stat().st_size / (1024 * 1024)
    snaps = len(list(BACKUP_DIR.glob("crypto_data_*.db")))
    print(f"Backed up to {dest} ({size_mb:.1f} MB). {snaps} snapshot(s) kept in {BACKUP_DIR}.")


if __name__ == "__main__":
    main()
