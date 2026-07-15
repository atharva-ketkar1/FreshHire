from dataclasses import dataclass


@dataclass
class RawJob:
    """A job posting as returned by an ATS, before dedup/persistence."""

    ats_job_id: str
    title: str
    location: str
    url: str
    description: str  # plain text, truncated
