"""SQLite persistence.

Timestamps are ISO 8601 UTC strings. first_seen_at is set exactly once,
on insert, and never touched again — it is the freshness signal.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from freshhire.models import RawJob

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    ats             TEXT NOT NULL,
    slug            TEXT NOT NULL,
    active          INTEGER NOT NULL DEFAULT 1,
    first_polled_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER NOT NULL REFERENCES companies(id),
    fingerprint         TEXT NOT NULL UNIQUE,
    ats_job_id          TEXT,
    title               TEXT NOT NULL,
    location            TEXT,
    url                 TEXT NOT NULL,
    description_snippet TEXT,
    relevant            INTEGER NOT NULL DEFAULT 0,
    is_baseline         INTEGER NOT NULL DEFAULT 0,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    closed_at           TEXT,
    alerted_at          TEXT,
    sheeted_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_fresh ON jobs(relevant, alerted_at, first_seen_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # migrations for DBs created before a column existed
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "sheeted_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN sheeted_at TEXT")
        conn.commit()
    return conn


def sync_companies(conn: sqlite3.Connection, companies: list[dict]) -> None:
    """Upsert companies from config; deactivate ones removed from it."""
    names = [c["name"] for c in companies]
    for c in companies:
        conn.execute(
            """INSERT INTO companies (name, ats, slug) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET ats=excluded.ats, slug=excluded.slug, active=1""",
            (c["name"], c["ats"], c["slug"]),
        )
    placeholders = ",".join("?" * len(names)) or "''"
    conn.execute(f"UPDATE companies SET active=0 WHERE name NOT IN ({placeholders})", names)
    conn.commit()


def active_companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM companies WHERE active=1 ORDER BY name").fetchall()


def record_sighting(
    conn: sqlite3.Connection,
    company_id: int,
    fp: str,
    job: RawJob,
    relevant: bool,
    is_baseline: bool,
    now: str,
) -> str:
    """Insert or refresh one posting. Returns 'new', 'seen', or 'reopened'."""
    row = conn.execute("SELECT id, closed_at FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO jobs (company_id, fingerprint, ats_job_id, title, location, url,
                                 description_snippet, relevant, is_baseline, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company_id, fp, job.ats_job_id, job.title, job.location, job.url,
             job.description[:1000], int(relevant), int(is_baseline), now, now),
        )
        return "new"
    reopened = row["closed_at"] is not None
    # A repost keeps its original first_seen_at — that's the whole point.
    # relevant is refreshed with the full live description so filter changes
    # propagate to open jobs on the next poll (rescore only sees snippets).
    conn.execute(
        "UPDATE jobs SET last_seen_at=?, closed_at=NULL, ats_job_id=?, url=?, relevant=? WHERE id=?",
        (now, job.ats_job_id, job.url, int(relevant), row["id"]),
    )
    return "reopened" if reopened else "seen"


def close_missing(conn: sqlite3.Connection, company_id: int, seen_fps: set[str], now: str) -> int:
    """Mark jobs that vanished from the board as closed. Only call after a successful fetch."""
    rows = conn.execute(
        "SELECT id, fingerprint FROM jobs WHERE company_id=? AND closed_at IS NULL", (company_id,)
    ).fetchall()
    missing = [r["id"] for r in rows if r["fingerprint"] not in seen_fps]
    conn.executemany("UPDATE jobs SET closed_at=? WHERE id=?", [(now, i) for i in missing])
    return len(missing)


def mark_polled(conn: sqlite3.Connection, company_id: int, now: str) -> None:
    conn.execute(
        "UPDATE companies SET first_polled_at=COALESCE(first_polled_at, ?) WHERE id=?",
        (now, company_id),
    )


def fresh_unalerted(conn: sqlite3.Connection, hours: int) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return conn.execute(
        """SELECT jobs.*, companies.name AS company_name
           FROM jobs JOIN companies ON companies.id = jobs.company_id
           WHERE jobs.relevant=1 AND jobs.is_baseline=0 AND jobs.alerted_at IS NULL
                 AND jobs.closed_at IS NULL AND jobs.first_seen_at >= ?
           ORDER BY jobs.first_seen_at DESC""",
        (cutoff,),
    ).fetchall()


def mark_alerted(conn: sqlite3.Connection, job_ids: list[int], now: str) -> None:
    conn.executemany("UPDATE jobs SET alerted_at=? WHERE id=?", [(now, i) for i in job_ids])
    conn.commit()


def alerted_unsheeted(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Alerted jobs not yet appended to the tracker sheet (still relevant only,
    so jobs re-scored out by a filter change don't get backfilled)."""
    return conn.execute(
        """SELECT jobs.*, companies.name AS company_name
           FROM jobs JOIN companies ON companies.id = jobs.company_id
           WHERE jobs.relevant=1 AND jobs.alerted_at IS NOT NULL AND jobs.sheeted_at IS NULL
           ORDER BY jobs.first_seen_at""",
    ).fetchall()


def mark_sheeted(conn: sqlite3.Connection, job_ids: list[int], now: str) -> None:
    conn.executemany("UPDATE jobs SET sheeted_at=? WHERE id=?", [(now, i) for i in job_ids])
    conn.commit()
