"""Delete complete evaluation user scopes after the configured retention period.

Dry-run by default. Schedule --apply on the deployed host at least daily.
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path


def purge(db_path: Path, days: int, apply: bool) -> dict[str, int]:
    if days < 1:
        raise ValueError("days must be positive")
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    cutoff = time.time() - days * 86400
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if apply:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA secure_delete=ON")
            # Acquire the writer lock before selecting scopes. A concurrent Add
            # cannot make a selected user fresh between the SELECT and DELETE.
            conn.execute("BEGIN IMMEDIATE")
        scopes = conn.execute(
            "SELECT user_id,COUNT(*) AS records FROM memories GROUP BY user_id HAVING MAX(created_at) < ?",
            (cutoff,),
        ).fetchall()
        records = sum(int(row["records"]) for row in scopes)
        if apply:
            has_fts = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
            ).fetchone() is not None
            for row in scopes:
                user_id = row["user_id"]
                if has_fts:
                    conn.execute("DELETE FROM memories_fts WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM memory_entities WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM memory_relations WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM add_requests WHERE user_id=?", (user_id,))
                conn.execute("DELETE FROM memories WHERE user_id=?", (user_id,))
            conn.commit()
            if records:
                # Rebuild freed pages, then truncate the WAL that may retain
                # source text from earlier writes. Backups/logs remain an
                # independent deployment responsibility.
                conn.execute("VACUUM")
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint[0] != 0:
                    raise RuntimeError("WAL checkpoint is busy; retry cleanup in a maintenance window")
        return {"scopes": len(scopes), "records": records, "applied": int(apply)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    print(purge(arguments.db, arguments.days, arguments.apply))


if __name__ == "__main__":
    main()
