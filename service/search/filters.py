"""Structured filters for Smart Search: after:, before:, in:, from:, type:.

Deterministic predicates over chunk metadata, never left to a model. The
searched text is one document, so the metadata is whatever the text itself
carries:

- ``after:`` / ``before:`` use the dates written in the text (2024-03-05,
  2024/3/5, March 5, 2024, 5 March 2024). A chunk with no date of its own takes
  the closest date written before it; a chunk with none at all cannot satisfy a
  date filter. ``after:2024`` means on or after 2024-01-01, ``before:2024-03``
  means earlier than 2024-03-01.
- ``from:`` matches the sender of the message a chunk belongs to: the closest
  ``From:`` line at or before the end of the chunk.
- ``in:code`` / ``in:<heading>`` keep chunks inside a code block, or under a
  heading whose text contains the value.
- ``type:`` is one of code, table, list, link/url, heading, quote.

A filter whose value cannot be read (``after:soon``, ``type:banana``) is not
applied and is reported in ``unapplied`` so the UI can say so.
"""
from __future__ import annotations

import bisect
import re
from datetime import date

from service.search.chunker import Chunk
from service.search.lexical import fold

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
_MON = "|".join(list(_MONTHS) + [m[:3] for m in _MONTHS])
_ISO_RE = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_MDY_RE = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DMY_RE = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\.?,?\s+(\d{{4}})\b", re.I)
_SPEC_RE = re.compile(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$")
_FENCE_RE = re.compile(r"```")
_HEADING_RE = re.compile(r"^(?:#{1,6}\s+(.+?)|(?:section|chapter|part)\s+\w+[.:]?\s*(.*))\s*$",
                         re.I | re.M)
_FROM_RE = re.compile(r"^from:[ \t]*(.+)$", re.I | re.M)
_URL_RE = re.compile(r"https?://|www\.", re.I)
_TYPES = {"code", "table", "list", "link", "url", "heading", "quote"}


def _month(name: str) -> int:
    return _MONTHS.get(name.lower()) or [v for k, v in _MONTHS.items() if k.startswith(name.lower()[:3])][0]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_bound(spec: str) -> date | None:
    """First day of the period a filter value names, or None if unreadable."""
    m = _SPEC_RE.match(spec.strip())
    if not m:
        return None
    return _mk(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))


def _dates_in(text: str) -> list[tuple[int, date]]:
    found: list[tuple[int, date]] = []
    for m in _ISO_RE.finditer(text):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in _MDY_RE.finditer(text):
        d = _mk(int(m.group(3)), _month(m.group(1)), int(m.group(2)))
        if d:
            found.append((m.start(), d))
    for m in _DMY_RE.finditer(text):
        d = _mk(int(m.group(3)), _month(m.group(2)), int(m.group(1)))
        if d:
            found.append((m.start(), d))
    found.sort()
    return found


def _is_code(t: str) -> bool:
    if _FENCE_RE.search(t):
        return True
    lines = [ln for ln in t.splitlines() if ln.strip()]
    return len(lines) >= 2 and sum(ln.startswith(("    ", "\t")) for ln in lines) * 2 >= len(lines)


def _kind_ok(kind: str, t: str) -> bool:
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if kind == "code":
        return _is_code(t)
    if kind == "table":
        return sum(ln.count("|") >= 2 for ln in lines) >= 2
    if kind == "list":
        return sum(bool(re.match(r"([-*•]|\d+[.)])\s+", ln)) for ln in lines) >= 2
    if kind in ("link", "url"):
        return bool(_URL_RE.search(t))
    if kind == "heading":
        return bool(re.search(r"^#{1,6}\s", t, re.M))
    if kind == "quote":
        return any(ln.startswith(">") for ln in lines)
    return False


def allowed_chunks(text: str, chunks: list[Chunk], filters: dict[str, str]
                   ) -> tuple[set[int] | None, list[str]]:
    """Chunk indexes that satisfy every readable filter (None = no filter in
    force), plus the filter names that could not be applied."""
    if not filters:
        return None, []
    ok = set(range(len(chunks)))
    unapplied: list[str] = []

    after = before = None
    if "after" in filters:
        after = parse_bound(filters["after"])
        if after is None:
            unapplied.append("after")
    if "before" in filters:
        before = parse_bound(filters["before"])
        if before is None:
            unapplied.append("before")
    if after or before:
        dates = _dates_in(text)
        positions = [p for p, _ in dates]
        keep = set()
        for c in chunks:
            own = [d for p, d in dates if c.start <= p < c.end]
            if not own:
                i = bisect.bisect_left(positions, c.start) - 1
                own = [dates[i][1]] if i >= 0 else []
            if any((after is None or d >= after) and (before is None or d < before)
                   for d in own):
                keep.add(c.idx)
        ok &= keep

    if "from" in filters:
        want = fold(filters["from"]).strip()
        froms = [(m.start(), fold(m.group(1))) for m in _FROM_RE.finditer(text)]
        positions = [p for p, _ in froms]
        keep = set()
        for c in chunks:
            i = bisect.bisect_left(positions, c.end) - 1
            if i >= 0 and want in froms[i][1]:
                keep.add(c.idx)
        ok &= keep

    scope = filters.get("in")
    if scope:
        want = fold(scope).strip()
        if want == "code":
            ok &= {c.idx for c in chunks if _is_code(c.text)}
        else:
            heads = [(m.start(), fold(m.group(1) or m.group(0))) for m in _HEADING_RE.finditer(text)]
            positions = [p for p, _ in heads]
            keep = set()
            for c in chunks:
                i = bisect.bisect_right(positions, c.start) - 1
                governing = [heads[i][1]] if i >= 0 else []
                inside = [h for p, h in heads if c.start <= p < c.end]
                if any(want in h for h in governing + inside):
                    keep.add(c.idx)
            ok &= keep

    kind = filters.get("type")
    if kind:
        k = fold(kind).strip()
        if k in _TYPES:
            ok &= {c.idx for c in chunks if _kind_ok(k, c.text)}
        else:
            unapplied.append("type")
    return ok, unapplied
