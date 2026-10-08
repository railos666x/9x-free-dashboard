"""News noise filter: wire-copy dedup + outlet corroboration.

2026-09-30: the live pool repeatedly carried the same story 2–3× from one
outlet (BTC ETF-inflow copies), inflating the sentiment window and pushing
directional trades on single-source noise. This module:

  1. dedupes near-identical headlines (wire copies, syndication) BEFORE
     the LLM window is built, and
  2. reports how many DISTINCT outlets corroborate a symbol's story, so
     signal_for can require multi-outlet agreement — or one very strong
     story — before risking money.

Stdlib only (difflib) — no new dependencies.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Source labels that share copy (aggregators/reprints) — treat as one voice.
_SAME_VOICE = {
    "reuters": ("reuters", "reuters via investing"),
    "investing": ("investing.com", "investing", "investingcom"),
}

_TITLE_STRIP = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def _norm_title(title: str) -> str:
    """Normalize a headline for similarity: lowercase, punctuation-free,
    trailing source suffixes (' | Reuters', ' - CNBC') removed."""
    t = str(title or "").lower()
    for sep in (" | ", " - ", " – "):
        idx = t.rfind(sep)
        if idx > 25:  # keep the headline body, drop only a real suffix
            t = t[:idx]
    t = _TITLE_STRIP.sub(" ", t)
    return _WS.sub(" ", t).strip()


def _norm_source(source: str) -> str:
    s = str(source or "").strip().lower()
    for canon, aliases in _SAME_VOICE.items():
        if s in aliases:
            return canon
    return s or "unknown"


def dedupe_headlines(rows: list[dict], threshold: float = 0.85
                     ) -> tuple[list[dict], int]:
    """Drop near-duplicate stories, newest first. Returns (kept, dup_count).

    The first occurrence (newest, given newest-first input order) wins;
    near-identical later copies are suppressed. Titles differing only in
    the age suffix are already handled by normalization.
    """
    kept: list[dict] = []
    kept_norms: list[str] = []
    dups = 0
    for r in rows:
        n = _norm_title(r.get("title", ""))
        if not n:
            dups += 1
            continue
        is_dup = False
        for k in kept_norms:
            if SequenceMatcher(None, n, k).ratio() >= threshold:
                is_dup = True
                break
        if is_dup:
            dups += 1
            continue
        kept.append(r)
        kept_norms.append(n)
    return kept, dups


def corroboration_stats(rows: list[dict], hints: list[str]) -> dict:
    """How many distinct outlets cover this symbol, and with what weight.

    rows: headline dicts (may come straight from fetch_headlines).
    hints: COIN_HINTS keywords for the symbol.
    """
    from reviewer import _mentions  # lazy — avoids import cost

    outlets: set[str] = set()
    stories = 0
    signed = 0.0
    strongest = 0.0
    for r in rows:
        if not _mentions(r, hints):
            continue
        stories += 1
        outlets.add(_norm_source(r.get("source", "")))
        try:
            s = float(r.get("score") or 0.0)
        except (TypeError, ValueError):
            s = 0.0
        signed += s
        strongest = max(strongest, abs(s))
    mean = (signed / stories) if stories else 0.0
    return {
        "story_count": stories,
        "distinct_outlets": len(outlets),
        "outlets": sorted(outlets),
        "mean_score": round(mean, 3),
        "strongest_score": round(strongest, 3),
    }


def corroboration_ok(stats: dict, min_outlets: int,
                     single_outlet_min_score: float) -> tuple[bool, str]:
    """Gate: trade only on multi-outlet agreement, or one clearly strong
    story. A lone weak headline is exactly the noise that loses money."""
    n = int(stats.get("distinct_outlets") or 0)
    strongest = float(stats.get("strongest_score") or 0.0)
    if n >= max(1, min_outlets):
        return True, ""
    if n == 1 and strongest >= single_outlet_min_score:
        return True, ""
    return False, (f"single-outlet noise ({n} outlet, strongest "
                   f"score {strongest:.2f} < {single_outlet_min_score:.2f}) "
                   "— wire copy must not move money")
