"""Digest formatting shared by all alert channels."""

from datetime import datetime, timezone


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def humanize_age(first_seen_at: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    minutes = max(0, int((now - _parse(first_seen_at)).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def format_slack(rows) -> str:
    """Slack mrkdwn digest. One line per job, grouped under a header."""
    n = len(rows)
    lines = [f":sparkles: *FreshHire: {n} fresh AI role{'s' if n != 1 else ''}*"]
    for r in rows:
        location = r["location"] or "Location unlisted"
        lines.append(
            f"• *{r['company_name']}* — <{r['url']}|{r['title']}> · {location} "
            f"· {humanize_age(r['first_seen_at'])}"
        )
    return "\n".join(lines)


def format_text(rows) -> str:
    """Plain-text digest for stdout / dry runs."""
    lines = [f"FreshHire: {len(rows)} fresh role(s)"]
    for r in rows:
        location = r["location"] or "Location unlisted"
        lines.append(
            f"- {r['company_name']} | {r['title']} | {location} "
            f"| {humanize_age(r['first_seen_at'])}\n  {r['url']}"
        )
    return "\n".join(lines)
