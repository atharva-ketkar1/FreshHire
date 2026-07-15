"""Lever public postings API.

Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
No auth required. Returns a JSON array of postings with plain-text
descriptions already provided in `descriptionPlain`.
"""

import re

import requests

from freshhire.models import RawJob

API = "https://api.lever.co/v0/postings/{slug}?mode=json"
DESCRIPTION_LIMIT = 5000

_WS_RE = re.compile(r"\s+")


def fetch_jobs(slug: str) -> list[RawJob]:
    resp = requests.get(API.format(slug=slug), timeout=30)
    resp.raise_for_status()
    jobs = []
    for j in resp.json():
        categories = j.get("categories") or {}
        description = _WS_RE.sub(" ", j.get("descriptionPlain") or "").strip()
        jobs.append(
            RawJob(
                ats_job_id=str(j.get("id") or ""),
                title=(j.get("text") or "").strip(),
                location=(categories.get("location") or "").strip(),
                url=j.get("hostedUrl") or "",
                description=description[:DESCRIPTION_LIMIT],
            )
        )
    return jobs
