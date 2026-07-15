"""Greenhouse public job board API.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
No auth required. `content` is HTML-escaped HTML, so it must be unescaped
before tags are stripped.
"""

import html
import re

import requests

from freshhire.models import RawJob

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
# generous: experience requirements often sit deep in long postings
DESCRIPTION_LIMIT = 20000

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(content: str) -> str:
    text = html.unescape(content or "")
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def fetch_jobs(slug: str) -> list[RawJob]:
    resp = requests.get(API.format(slug=slug), timeout=30)
    resp.raise_for_status()
    jobs = []
    for j in resp.json().get("jobs", []):
        jobs.append(
            RawJob(
                ats_job_id=str(j["id"]),
                title=(j.get("title") or "").strip(),
                location=((j.get("location") or {}).get("name") or "").strip(),
                url=j.get("absolute_url") or "",
                description=_strip_html(j.get("content") or "")[:DESCRIPTION_LIMIT],
            )
        )
    return jobs
