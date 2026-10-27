"""Export a run's real output for the Qoder Sites demo.

The site is a snapshot, not a second implementation: every number, bullet and
hit here is produced by the same `collect`/`index`/`report`/`search` path the CLI
uses. Nothing in this module invents content, and the export records the query
it ran so a reviewer can re-run it locally and compare.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from recap.config import Config
from recap.indexer import build_docs
from recap.pctx import PowerContext
from recap.report import compose, render_markdown
from recap.store import RecapIndex

PROBE_LIMIT = 5
SNIPPET = 320
# The raw statement's projection; pylibseekdb's Cursor exposes no `description`.
SQL_COLUMNS = ("_id", "ref", "ft_hit", "vec_dist")


def _probe_queries(report: dict[str, Any]) -> list[str]:
    """Queries taken from this week's own traces, not hand-written examples."""
    probes: list[str] = []
    for item in (report["progress"][:1] + report["decisions"][:1] + report["next_week"][:1]):
        text = str(item.get("text", "")).strip()[:120]
        if text and text not in probes:
            probes.append(text)
    return probes


def _top_token(texts: list[str]) -> str:
    """The corpus's own most frequent identifier, used as the full-text term so the
    single-statement demo matches on real vocabulary instead of a hand-picked word."""
    counts = Counter(token for text in texts for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{3,}", text or ""))
    return counts.most_common(1)[0][0] if counts else "seekdb"


def _elide_vector(sql: str) -> str:
    """The query vector is a 1024-float literal; the statement's shape is the point."""
    return re.sub(r"\[-?[\d.eE+-]+(?:,-?[\d.eE+-]+){8,}\]", "[… query embedding …]", sql)


def export(cfg: Config, scope_id: str, since: date, until: date, week: str, out_path: Path) -> Path:
    index = RecapIndex(path=str(cfg.seekdb_path))

    async def build() -> dict[str, Any]:
        pc = PowerContext(cfg)
        report = await compose(scope_id, pc, index, since, until, week)
        docs = await build_docs(pc, scope_id, fallback=until)
        by_ref: dict[str, dict[str, Any]] = {}
        for doc in docs:
            by_ref.setdefault(doc.ref, {
                "text": doc.text[:900],
                "family": doc.family,
                "kind": doc.kind,
                "created_at": doc.created_at.isoformat(),
                "repo": doc.repo or "",
            })
        probes = []
        for query in _probe_queries(report):
            hits = index.search(query, limit=PROBE_LIMIT)
            probes.append({
                "query": query,
                "hits": [
                    {
                        "ref": hit["ref"],
                        "text": (hit["text"] or "")[:SNIPPET],
                        "distance": hit["distance"],
                        "kind": (hit["metadata"] or {}).get("kind"),
                        "created_at": (hit["metadata"] or {}).get("created_at"),
                    }
                    for hit in hits
                ],
            })
        phrase = _top_token([doc.text for doc in docs])
        # The window bound is the end of the report week, so the statement returns the
        # week's own rows; `since` would filter them all out.
        cutoff = until + timedelta(days=1)
        sql, rows = index.raw_hybrid_sql(phrase, index.embed(phrase), cutoff, limit=PROBE_LIMIT)
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "week": week,
            "window": {"since": since.isoformat(), "until": until.isoformat()},
            "scope_id": scope_id,
            "report": report,
            "markdown": render_markdown(report),
            "citations": by_ref,
            "probes": probes,
            "hybrid_sql": {
                "statement": _elide_vector(sql),
                "parameters": {"phrase": phrase, "cutoff": cutoff.isoformat(), "limit": PROBE_LIMIT},
                "columns": list(SQL_COLUMNS),
                "row_count": len(rows),
                "sample": [_round_distances(dict(zip(SQL_COLUMNS, row))) for row in rows[:PROBE_LIMIT]],
            },
            "index": {"collection": index.collection.name, "table": index.table(), "docs": index.count()},
        }

    payload = asyncio.run(build())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return out_path


def _round_distances(row: dict[str, Any]) -> dict[str, Any]:
    clean = dict(row)
    for key, value in clean.items():
        if isinstance(value, float):
            clean[key] = round(value, 6)
    return clean


MEMORY_KINDS = {"decision", "constraint", "next_step", "state", "fact"}
SEED_TITLE = 200
SEED_BODY = 1800


def _flat(value: str, limit: int) -> str:
    """Same collapsing the Function applies, so nothing is refused on import."""
    return " ".join((value or "").split())[:limit]


def seed_items(docs: list[Any], author: str) -> list[dict[str, Any]]:
    """Map the mirrored traces onto the site board's vocabulary.

    Handoff claims and stored reports are process records about the pipeline, not
    work traces, so the cloud board composes only from sources and memories. The
    same is true of the JSON envelopes PowerContext writes back as sources: their
    first line is `{`, so they would fill the board with unreadable rows.
    """
    from recap.ingest import week_label

    items: list[dict[str, Any]] = []
    for doc in docs:
        lines = (doc.text or "").splitlines()
        first = (lines[0] if lines else "").strip()
        if doc.ref.startswith("source/"):
            kind = "commit" if doc.kind == "evidence" else "note"
            title = _flat(re.sub(r"^#+\s*", "", first), SEED_TITLE)
        elif doc.ref.startswith("atomic-memory/"):
            if first.startswith(("{", '"')):
                continue
            kind = doc.kind if doc.kind in MEMORY_KINDS else "note"
            title = _flat(first or doc.title, SEED_TITLE)
        else:
            continue
        if not title or first.startswith(("{", '"')):
            continue
        # The board requires a body; a one-line memory repeats itself there so the
        # import is not refused for having nothing to show under the citation.
        body = _flat(" ".join(lines[1:]), SEED_BODY) or title
        items.append(
            {
                "cite": doc.ref,
                "week": week_label(doc.created_at),
                "day": doc.created_at.isoformat(),
                "kind": kind,
                "title": title,
                "body": body,
                "repo": doc.repo or "",
                "author": author,
            }
        )
    return items


def export_seed(cfg: Config, scope_id: str, out_path: Path, fallback: date) -> Path:
    """Write the archive the deployed site imports, straight from PowerContext."""
    index = RecapIndex(path=str(cfg.seekdb_path))

    async def build() -> dict[str, Any]:
        pc = PowerContext(cfg)
        docs = await build_docs(pc, scope_id, fallback=fallback)
        author = cfg.repos[0].author if cfg.repos else "recap"
        items = seed_items(docs, author)
        return {
            "meta": {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scope_id": scope_id,
                "docs": len(items),
                "skipped_process_docs": len(docs) - len(items),
                "collection": index.collection.name,
                "table": index.table(),
                "author": author,
            },
            "items": items,
        }

    payload = asyncio.run(build())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return out_path
