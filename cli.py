#!/usr/bin/env python3
"""FreshHire CLI.

  python cli.py poll                     fetch all boards, record sightings
  python cli.py digest [--dry-run]       alert on fresh relevant jobs
  python cli.py run                      poll + digest (what the cron calls)
  python cli.py list-new [--hours N]     print fresh relevant jobs, no side effects
"""

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from freshhire import db
from freshhire.alerts import digest as digest_fmt
from freshhire.alerts import sheets, slack
from freshhire.filters import load_filter
from freshhire.poll import poll_all

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "freshhire.db"
COMPANIES_PATH = ROOT / "config" / "companies.yaml"
FILTERS_PATH = ROOT / "config" / "filters.yaml"


def load_companies() -> list[dict]:
    with open(COMPANIES_PATH) as f:
        return yaml.safe_load(f)["companies"]


def cmd_poll(conn, args) -> int:
    db.sync_companies(conn, load_companies())
    role_filter = load_filter(FILTERS_PATH)
    companies = [dict(r) for r in db.active_companies(conn)]
    stats = poll_all(conn, companies, role_filter)
    print(
        f"[poll] {stats.companies_ok} boards ok, {stats.companies_failed} failed | "
        f"{stats.new} new ({stats.new_relevant} relevant), "
        f"{stats.reopened} reopened, {stats.closed} closed"
    )
    return 0 if stats.companies_ok > 0 else 1


def cmd_digest(conn, args) -> int:
    role_filter = load_filter(FILTERS_PATH)
    hours = args.hours or role_filter.recency_hours
    rows = db.fresh_unalerted(conn, hours)
    if not rows:
        print(f"[digest] nothing fresh in the last {hours}h — no alert sent")
        return 0

    if args.dry_run:
        print(digest_fmt.format_text(rows))
        print(f"[digest] dry run — {len(rows)} job(s) NOT marked as alerted")
        return 0

    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if webhook:
        text = digest_fmt.format_slack(rows)
        sheet_id = os.environ.get("GOOGLE_SHEET_ID")
        if sheet_id:
            text += f"\n<{sheets.sheet_url(sheet_id)}|Open tracker sheet>"
        slack.send(webhook, text)
        print(f"[digest] sent {len(rows)} job(s) to Slack")
    elif args.stdout:
        print(digest_fmt.format_text(rows))
    else:
        print(
            "[digest] SLACK_WEBHOOK_URL not set. Use --dry-run to preview or "
            "--stdout to deliver to the terminal and mark jobs alerted.",
            file=sys.stderr,
        )
        return 1
    db.mark_alerted(conn, [r["id"] for r in rows], db.utcnow())
    return 0


def cmd_sheet(conn, args) -> int:
    """Append alerted-but-unsheeted jobs to the Google Sheet tracker."""
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sheet_id or not sa_json:
        print("[sheet] GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON not set — skipping tracker sync")
        return 0
    rows_db = db.alerted_unsheeted(conn)
    if not rows_db:
        print("[sheet] tracker already up to date")
        return 0
    rows = [
        sheets.job_row(sheets.fmt_local(r["first_seen_at"]), r["company_name"], r["title"],
                       r["location"] or "", r["url"])
        for r in rows_db
    ]
    added = sheets.append_jobs(json.loads(sa_json), sheet_id, rows)
    db.mark_sheeted(conn, [r["id"] for r in rows_db], db.utcnow())
    print(f"[sheet] appended {added} job(s) to the tracker ({len(rows) - added} already present)")
    return 0


def cmd_run(conn, args) -> int:
    rc = cmd_poll(conn, args)
    if rc:
        return rc
    rc_digest = cmd_digest(conn, args)
    # sheet sync runs even if digest failed — it backfills previously
    # alerted jobs and loses nothing by trying
    rc_sheet = cmd_sheet(conn, args)
    return rc_digest or rc_sheet


def cmd_list_new(conn, args) -> int:
    rows = db.fresh_unalerted(conn, args.hours or 24)
    print(digest_fmt.format_text(rows) if rows else "No fresh relevant jobs.")
    return 0


def cmd_rescore(conn, args) -> int:
    """Re-run filters.yaml against every stored job (after tuning filters).

    Uses the stored description snippet, which is shorter than what the
    filter saw at poll time, so borderline description-based matches may
    differ slightly until the next poll refreshes them.
    """
    role_filter = load_filter(FILTERS_PATH)
    rows = conn.execute("SELECT id, title, location, description_snippet, relevant FROM jobs").fetchall()
    flipped = 0
    for r in rows:
        relevant = role_filter.is_relevant(r["title"], r["description_snippet"] or "", r["location"] or "")
        if int(relevant) != r["relevant"]:
            conn.execute("UPDATE jobs SET relevant=? WHERE id=?", (int(relevant), r["id"]))
            flipped += 1
    conn.commit()
    total_relevant = conn.execute("SELECT COUNT(*) FROM jobs WHERE relevant=1").fetchone()[0]
    print(f"[rescore] {len(rows)} jobs re-scored, {flipped} flipped, {total_relevant} now relevant")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="freshhire")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("poll", help="fetch all boards and record sightings")

    p_digest = sub.add_parser("digest", help="alert on fresh relevant jobs")
    p_run = sub.add_parser("run", help="poll then digest")
    for p in (p_digest, p_run):
        p.add_argument("--hours", type=int, default=None, help="recency window (default: filters.yaml)")
        p.add_argument("--dry-run", action="store_true", help="print digest, do not send or mark alerted")
        p.add_argument("--stdout", action="store_true", help="deliver to terminal instead of Slack")

    p_list = sub.add_parser("list-new", help="print fresh relevant jobs (read-only)")
    p_list.add_argument("--hours", type=int, default=24)

    sub.add_parser("rescore", help="re-apply filters.yaml to all stored jobs")
    sub.add_parser("sheet", help="sync alerted jobs to the Google Sheet tracker")

    args = parser.parse_args()
    if not hasattr(args, "hours"):
        args.hours = None

    conn = db.connect(DB_PATH)
    try:
        handler = {
            "poll": cmd_poll,
            "digest": cmd_digest,
            "run": cmd_run,
            "list-new": cmd_list_new,
            "rescore": cmd_rescore,
            "sheet": cmd_sheet,
        }[args.command]
        return handler(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
