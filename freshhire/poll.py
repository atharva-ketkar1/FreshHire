"""Poll orchestrator: fetch every active company's board, upsert sightings,
close vanished postings.

A company's very first poll flags every job as baseline (is_baseline=1) so
the initial import never triggers alerts — only jobs that appear on a
board *after* we started watching it count as fresh.

Fetch failures are logged and skipped; close_missing is never run on a
failed fetch, so a flaky API can't mass-"close" a company's jobs.
"""

import sys
from dataclasses import dataclass, field

from freshhire.ats import FETCHERS
from freshhire.db import close_missing, mark_polled, record_sighting, utcnow
from freshhire.dedupe import fingerprint
from freshhire.filters import RoleFilter


@dataclass
class PollStats:
    companies_ok: int = 0
    companies_failed: int = 0
    new: int = 0
    new_relevant: int = 0
    reopened: int = 0
    closed: int = 0
    errors: list[str] = field(default_factory=list)


def poll_all(conn, companies, role_filter: RoleFilter) -> PollStats:
    stats = PollStats()
    for company in companies:
        fetch = FETCHERS.get(company["ats"])
        if fetch is None:
            stats.errors.append(f"{company['name']}: unsupported ATS {company['ats']!r}")
            stats.companies_failed += 1
            continue
        try:
            raw_jobs = fetch(company["slug"])
        except Exception as exc:  # noqa: BLE001 - one bad board must not kill the poll
            stats.errors.append(f"{company['name']}: {exc}")
            stats.companies_failed += 1
            continue

        now = utcnow()
        is_first_poll = company["first_polled_at"] is None
        seen_fps: set[str] = set()
        for job in raw_jobs:
            if not job.title or not job.url:
                continue
            fp = fingerprint(company["name"], job.title, job.location)
            if fp in seen_fps:
                continue  # duplicate posting within the same board snapshot
            seen_fps.add(fp)
            relevant = role_filter.is_relevant(job.title, job.description, job.location)
            outcome = record_sighting(conn, company["id"], fp, job, relevant, is_first_poll, now)
            if outcome == "new":
                stats.new += 1
                if relevant and not is_first_poll:
                    stats.new_relevant += 1
            elif outcome == "reopened":
                stats.reopened += 1

        stats.closed += close_missing(conn, company["id"], seen_fps, now)
        mark_polled(conn, company["id"], now)
        conn.commit()
        stats.companies_ok += 1

    for err in stats.errors:
        print(f"[poll] ERROR {err}", file=sys.stderr)
    return stats
