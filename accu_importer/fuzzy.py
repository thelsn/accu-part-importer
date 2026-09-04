from __future__ import annotations

import re

from rapidfuzz import fuzz

_SPLIT = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    return " ".join(_SPLIT.split(text.casefold())).strip()


def _token_overlap(query: str, haystack: str) -> float:
    q_tokens = [t for t in _norm(query).split() if t]
    if not q_tokens:
        return 0.0
    h_tokens = set(_norm(haystack).split())
    if not h_tokens:
        return 0.0
    hits = sum(1 for t in q_tokens if t in h_tokens or any(t in h or h in t for h in h_tokens if len(t) >= 2))
    return 100.0 * hits / len(q_tokens)


def field_score(query: str, field: str) -> float:
    if not query or not field:
        return 0.0
    q = _norm(query)
    h = _norm(field)
    if not q or not h:
        return 0.0
    wratio = float(fuzz.WRatio(q, h))
    token_set = float(fuzz.token_set_ratio(q, h))
    partial = float(fuzz.partial_ratio(q, h))
    overlap = _token_overlap(q, h)
    return 0.40 * wratio + 0.30 * token_set + 0.15 * partial + 0.15 * overlap


def product_fuzzy_score(query: str, reference: str, title: str, category: str, attributes: str) -> float:
    """Blend RapidFuzz scorers across Accu product fields.

    Algolia already retrieves candidates from the 750k+ catalogue. This
    re-ranks those hits so typos, token order, and missing words still
    bubble the right fastener to the top.
    """
    scores = [
        field_score(query, reference) * 1.15,
        field_score(query, title),
        field_score(query, category) * 0.85,
        field_score(query, attributes) * 0.90,
    ]
    return max(0.0, min(100.0, max(scores)))


def combined_rank_score(fuzzy: float, algolia_index: int, hit_count: int) -> float:
    if hit_count <= 1:
        position = 100.0
    else:
        position = 100.0 * (1.0 - (algolia_index / max(hit_count - 1, 1)))
    return 0.62 * fuzzy + 0.38 * position
