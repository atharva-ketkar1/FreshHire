"""Role relevance filtering, driven entirely by config/filters.yaml.

Three stages, in order:
  1. exclude_title: any match on the title -> irrelevant, stop.
  2. include_title: any match on the title -> relevant.
  3. conditional_title: title term matches AND (a title_context term appears
     in the title OR at least min_desc_matches distinct desc_context phrases
     appear in the description).

Bare terms like "ai"/"ml" are only trusted in titles; descriptions require
multiple distinct desc_context phrases, because every posting at an AI
company mentions "LLM" once somewhere in its boilerplate.

All matching is word-boundary based, so "ai" never matches "email".
"""

import re
from pathlib import Path

import yaml

# --- US location detection (positive-match: no US signal => not US) ---
_US_STATES = (
    "alabama alaska arizona arkansas california colorado connecticut delaware florida georgia "
    "hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts "
    "michigan minnesota mississippi missouri montana nebraska nevada ohio oklahoma oregon "
    "pennsylvania tennessee texas utah vermont virginia washington wisconsin wyoming"
).split() + ["new hampshire", "new jersey", "new mexico", "new york", "north carolina",
             "north dakota", "rhode island", "south carolina", "south dakota", "west virginia"]
_US_CITIES = [
    "san francisco", "sf bay", "nyc", "brooklyn", "seattle", "mountain view", "palo alto",
    "sunnyvale", "san jose", "santa clara", "cupertino", "menlo park", "redwood city",
    "san mateo", "south san francisco", "foster city", "oakland", "berkeley", "emeryville",
    "los angeles", "santa monica", "san diego", "irvine", "austin", "dallas", "houston",
    "boston", "chicago", "denver", "boulder", "atlanta", "miami", "bellevue", "redmond",
    "kirkland", "phoenix", "salt lake city", "pittsburgh", "philadelphia", "minneapolis",
    "ann arbor", "raleigh", "durham", "nashville", "columbus", "st. louis", "washington dc",
]
_US_PATTERNS = [
    re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])")
    for t in ["united states", "usa", "u.s.", "us"] + _US_STATES + _US_CITIES
]
# "City, ST" state abbreviations (comma-anchored so "Remote or Hybrid" can't match OR)
_US_STATE_ABBREV_RE = re.compile(
    r",\s*(al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|mt|ne|"
    r"nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy|dc)(?![a-z0-9])"
)

# --- minimum years-of-experience requirements in descriptions ---
_YEARS_BEFORE_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?[^.\n]{0,50}?experience")
_YEARS_AFTER_RE = re.compile(r"experience[^.\n]{0,50}?(\d{1,2})\s*\+?\s*years?")


def is_us_location(location: str) -> bool:
    """True if the location names anywhere in the US (multi-location strings
    pass if any listed location is in the US). Empty locations pass — most
    watched companies are US-based and blank usually means HQ/remote-US."""
    loc = (location or "").lower()
    if not loc.strip():
        return True
    return bool(_US_STATE_ABBREV_RE.search(loc)) or any(p.search(loc) for p in _US_PATTERNS)


# --- fit scoring (0-100): how well a passing job matches an early-career
# AI-engineer profile. Title boosts take the max matching tier, not a sum. ---
_TITLE_BOOSTS = [
    (30, ["new grad", "new graduate", "university grad", "early career",
          "entry level", "junior", "engineer i", "graduate"]),
    (25, ["ai engineer", "forward deployed", "fde", "applied ai"]),
    (20, ["machine learning engineer", "ml engineer", "mle", "machine learning researcher"]),
    (15, ["research engineer", "applied scientist", "llm", "genai"]),
    (10, ["member of technical staff", "machine learning", "computer vision", "nlp"]),
]
_DESC_EARLY_TERMS = ["new grad", "recent graduate", "entry level", "0-1 years",
                     "0-2 years", "1-2 years", "early career", "all levels"]


def min_years_required(description: str) -> int | None:
    """Smallest years-of-experience figure stated near 'experience', or None.
    Using the minimum keeps '2+ years (5+ for senior)' postings alive."""
    desc = (description or "").lower()
    years = [
        int(m.group(1))
        for pattern in (_YEARS_BEFORE_RE, _YEARS_AFTER_RE)
        for m in pattern.finditer(desc)
        if int(m.group(1)) <= 20  # ignore "founded 25 years ago" noise
    ]
    return min(years) if years else None


def _term_re(term: str) -> re.Pattern:
    # "re:" prefix = raw regex (for terms needing exceptions, e.g. excluding
    # "staff" but not "member of technical staff"); anything else is a plain
    # word-boundary term.
    if term.startswith("re:"):
        return re.compile(term[3:].lower())
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


_TITLE_BOOSTS = [(pts, [_term_re(t) for t in terms]) for pts, terms in _TITLE_BOOSTS]
_DESC_EARLY_RES = [_term_re(t) for t in _DESC_EARLY_TERMS]


class RoleFilter:
    def __init__(self, cfg: dict):
        self.recency_hours = int(cfg.get("recency_hours", 24))
        self.us_only = bool(cfg.get("us_only", False))
        self.max_years = cfg.get("max_years_experience")
        self.exclude = [_term_re(t) for t in cfg.get("exclude_title", [])]
        self.include = [_term_re(t) for t in cfg.get("include_title", [])]
        self.conditional = [
            (
                _term_re(rule["title"]),
                [_term_re(t) for t in rule.get("title_context", [])],
                [_term_re(t) for t in rule.get("desc_context", [])],
                int(rule.get("min_desc_matches", cfg.get("min_desc_matches", 2))),
            )
            for rule in cfg.get("conditional_title", [])
        ]

    def is_relevant(self, title: str, description: str, location: str = "") -> bool:
        title_l = (title or "").lower()
        if any(p.search(title_l) for p in self.exclude):
            return False
        if self.us_only and not is_us_location(location):
            return False
        if not self._title_match(title_l, description):
            return False
        if self.max_years is not None:
            required = min_years_required(description)
            if required is not None and required > int(self.max_years):
                return False
        return True

    def evaluate(self, title: str, description: str, location: str = "") -> tuple[bool, int]:
        """(relevant, fit 0-100). Fit ranks passing jobs by profile match:
        base 40, + best title tier, + stated-experience friendliness,
        + early-career phrases in the description."""
        if not self.is_relevant(title, description, location):
            return False, 0
        title_l = (title or "").lower()
        desc_l = (description or "").lower()
        fit = 40
        fit += next((pts for pts, pats in _TITLE_BOOSTS
                     if any(p.search(title_l) for p in pats)), 0)
        required = min_years_required(description)
        if required is not None and required <= 1:
            fit += 20
        elif required is None:
            fit += 5
        else:  # 2 years stated — allowed, slightly less ideal
            fit += 10
        if any(p.search(desc_l) for p in _DESC_EARLY_RES):
            fit += 10
        return True, min(fit, 100)

    def _title_match(self, title_l: str, description: str) -> bool:
        if any(p.search(title_l) for p in self.include):
            return True
        desc_l = (description or "").lower()
        for title_pat, title_ctx, desc_ctx, min_desc in self.conditional:
            if not title_pat.search(title_l):
                continue
            if any(p.search(title_l) for p in title_ctx):
                return True
            if sum(1 for p in desc_ctx if p.search(desc_l)) >= min_desc:
                return True
        return False


def load_filter(path: str | Path) -> RoleFilter:
    with open(path) as f:
        return RoleFilter(yaml.safe_load(f) or {})
