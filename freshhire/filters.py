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


def _term_re(term: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


class RoleFilter:
    def __init__(self, cfg: dict):
        self.recency_hours = int(cfg.get("recency_hours", 24))
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

    def is_relevant(self, title: str, description: str) -> bool:
        title_l = (title or "").lower()
        if any(p.search(title_l) for p in self.exclude):
            return False
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
