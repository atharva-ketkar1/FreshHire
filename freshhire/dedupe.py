"""Fingerprinting for aggressive dedup.

Same company + same normalized title + same normalized location = one
record, regardless of how many times the ATS re-issues the posting under
a new job ID. first_seen_at on the original record is what counts.
"""

import hashlib
import re

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = (text or "").lower()
    text = _NON_ALNUM_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def fingerprint(company: str, title: str, location: str) -> str:
    key = "|".join([normalize(company), normalize(title), normalize(location)])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()
