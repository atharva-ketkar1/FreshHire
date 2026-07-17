"""Workday CxS public job API (no auth).

Search: POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
        body {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "..."}
Detail: GET  .../wday/cxs/{tenant}/{site}{externalPath}

Slug format in companies.yaml: "tenant.wdN/SiteName",
e.g. "walmart.wd5/WalmartExternal".

Enterprise boards carry thousands of postings, so instead of paging the
whole board we run a few targeted searches, then fetch full details (real
location + description) only for titles that look on-topic. Detail fetches
are capped per poll; anything past the cap is picked up on later polls.
"""

import html
import re
import time

import requests

from freshhire.models import RawJob

SEARCHES = ["machine learning", "artificial intelligence", "ai engineer"]
PAGES_PER_SEARCH = 5          # 20 results/page (API max) -> up to 100 hits/term
DETAIL_FETCH_CAP = 60         # per company per poll
DESCRIPTION_LIMIT = 20000
REQUEST_TIMEOUT = 15
# One slow/hanging tenant must not stall the whole poll. On budget overrun we
# raise (company skipped, retried next poll) rather than return a partial
# board, which would wrongly close every job we didn't get to.
FETCH_BUDGET_SECONDS = 150

# cheap prefilter so we don't fetch details for the sales/ops roles that
# fuzzy search drags in; the real RoleFilter decides afterwards
_TITLE_HINT_RE = re.compile(
    r"(?i)\b(ai|ml|machine learning|artificial intelligence|deep learning|llm|genai|"
    r"generative|nlp|computer vision|perception|data scien|research|engineer|developer)\b"
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(content: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(content or ""))).strip()


def _parse_slug(slug: str) -> tuple[str, str, str]:
    tenant_host, site = slug.split("/", 1)
    tenant, wd = tenant_host.split(".", 1)
    return tenant, wd, site


def fetch_jobs(slug: str) -> list[RawJob]:
    tenant, wd, site = _parse_slug(slug)
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    session = requests.Session()
    session.headers["Accept"] = "application/json"
    deadline = time.monotonic() + FETCH_BUDGET_SECONDS

    def check_budget():
        if time.monotonic() > deadline:
            raise RuntimeError(f"workday fetch budget ({FETCH_BUDGET_SECONDS}s) exceeded")

    postings: dict[str, dict] = {}
    for term in SEARCHES:
        for page in range(PAGES_PER_SEARCH):
            check_budget()
            resp = session.post(
                f"{base}/jobs",
                json={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": term},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            batch = resp.json().get("jobPostings", [])
            for p in batch:
                path = p.get("externalPath")
                if path:
                    postings.setdefault(path, p)
            if len(batch) < 20:
                break

    jobs = []
    fetched = 0
    for path, posting in postings.items():
        title = (posting.get("title") or "").strip()
        if not title or not _TITLE_HINT_RE.search(title):
            continue
        if fetched >= DETAIL_FETCH_CAP:
            break
        check_budget()
        fetched += 1
        try:
            detail = session.get(f"{base}{path}", timeout=REQUEST_TIMEOUT).json().get("jobPostingInfo", {})
        except Exception:
            continue
        locations = [detail.get("location") or ""] + (detail.get("additionalLocations") or [])
        jobs.append(
            RawJob(
                ats_job_id=str(detail.get("jobReqId") or (posting.get("bulletFields") or [path])[0]),
                title=(detail.get("title") or title).strip(),
                location="; ".join(l for l in locations if l),
                url=detail.get("externalUrl") or f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{path}",
                description=_strip_html(detail.get("jobDescription") or "")[:DESCRIPTION_LIMIT],
            )
        )
    return jobs
