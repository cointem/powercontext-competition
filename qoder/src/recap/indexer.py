"""Mirror PowerContext artifacts into the seekdb index.

The mirror is what lets Recap answer "show me everything in this window" and
"where else has this topic come up" — questions the Scope-scoped recall API is
not shaped for. It is rebuilt from PowerContext every run, so PowerContext stays
the single source of truth and the index is disposable.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from recap.config import Config
from recap.pctx import PowerContext
from recap.store import Doc, RecapIndex

KIND_LABEL = {
    "decision": "决定",
    "constraint": "约束",
    "state": "状态",
    "next_step": "下一步",
    "fact": "事实",
}


def _day_from(text: str, fallback: date) -> date:
    for token in text.replace('"', " ").split():
        if len(token) == 10 and token[4] == "-" and token[:4].isdigit():
            try:
                return date.fromisoformat(token)
            except ValueError:
                continue
    return fallback


def _trace_key(content: str) -> str:
    """Identify the trace a journal entry describes, independent of its text version.

    Re-collecting an edited note appends a new journal version (Source content is
    immutable), so the mirror would otherwise keep every rewrite as if it were a
    separate historical record.
    """
    sha = re.search(r"^commit ([0-9a-f]{7,40}) authored", content, re.MULTILINE)
    if sha:
        return f"commit:{sha.group(1)}"
    if content.startswith("#"):
        return "note:" + content.splitlines()[0]
    return ""


async def build_docs(pc: PowerContext, scope_id: str, fallback: date) -> list[Doc]:
    docs: list[Doc] = []

    for mem in await pc.active_memories(scope_id):
        docs.append(
            Doc(
                id=mem["ref"],
                text=mem["text"],
                family=mem["family"],
                kind=mem["kind"],
                ref=mem["ref"],
                created_at=fallback,
                title=KIND_LABEL.get(mem["kind"], mem["kind"]),
                scope_id=scope_id,
            )
        )

    journal_docs: dict[str, tuple[int, Doc]] = {}
    for entry in await pc.journal(scope_id, limit=500):
        content = entry["content"]
        kind = _source_kind(content)
        if kind == "memory-entry":
            # The journal keeps an envelope per remembered line, but the memory
            # itself is already mirrored from the artifact list above, with a
            # citation that resolves. Indexing the echo only adds noise.
            continue
        doc = Doc(
            id=f"source:{entry['source_id']}",
            text=content[:8000],
            family="source",
            kind=kind,
            ref=f"source/{entry['source_id']}",
            created_at=_day_from(content, fallback),
            title=_first_line(content),
            repo=_repo_tag(content),
            scope_id=scope_id,
        )
        key = _trace_key(content) or doc.id
        position = int(entry.get("position") or 0)
        if key not in journal_docs or position >= journal_docs[key][0]:
            journal_docs[key] = (position, doc)

    for _, doc in sorted(journal_docs.values(), key=lambda item: item[0]):
        docs.append(doc)

    for handoff in await pc.handoffs(scope_id):
        content = handoff.get("content") or {}
        for claim in _claims(content):
            docs.append(
                Doc(
                    id=f"{handoff['ref']}:{len(docs)}",
                    text=claim["text"],
                    family="handoff",
                    kind="claim",
                    ref=handoff["ref"],
                    created_at=fallback,
                    title=content.get("objective", "")[:120],
                    scope_id=scope_id,
                )
            )
    return docs


def _claims(content: dict[str, Any]) -> list[dict[str, Any]]:
    state = content.get("state") or []
    return [s for s in state if isinstance(s, dict) and s.get("text")]


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")[:160]


def _source_kind(text: str) -> str:
    head = text.lstrip()[:1]
    if head != "{":
        return "note" if text.startswith("#") else "evidence"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return "evidence"
    schema = str(payload.get("schema", ""))
    if "work-contract" in schema:
        return "work-contract"
    if "current-work-handoff" in schema:
        return "handoff-boundary"
    if "task-outcome" in schema:
        return "task-outcome"
    if "atomic-memory" in schema:
        return "memory-entry"
    return "structured"


def _repo_tag(text: str) -> str:
    if text.startswith("[") and "]" in text:
        return text[1 : text.index("]")]
    return ""


async def refresh(
    cfg: Config, pc: PowerContext, scope_id: str, index: RecapIndex, fallback: date
) -> tuple[int, int]:
    """Rebuild the mirror from PowerContext; returns (dropped, mirrored)."""
    dropped = index.delete_mirrored()
    return dropped, index.upsert(await build_docs(pc, scope_id, fallback))
