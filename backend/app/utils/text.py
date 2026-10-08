from __future__ import annotations

import re
from difflib import SequenceMatcher

URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
_TRAIL = ".,;:!?)]}'\""


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t]


def match_score(query: str, title: str) -> float:
    """0-100 similarity between a user's product query and a product title."""
    q, t = tokens(query), tokens(title)
    if not q or not t:
        return 0.0
    if normalize(query) == normalize(title):
        return 100.0
    qs, ts = set(q), set(t)
    inter = len(qs & ts)
    coverage = inter / len(qs)
    precision = inter / len(ts)
    ratio = SequenceMatcher(None, normalize(query), normalize(title)).ratio()
    return round(100 * (0.45 * coverage + 0.20 * precision + 0.35 * ratio), 1)


def clean_trailing(url: str) -> str:
    return url.rstrip(_TRAIL)


def strip_urls(text: str) -> str:
    return re.sub(r"\s{2,}", " ", URL_RE.sub("", text or "")).strip()
